import os
import shutil
import subprocess
import shlex
import copy
import multiprocessing as mp
import jstyleson


class Builder:
    def __init__(self,
                 artifactsPath='artifacts',
                 buildResultPath='build_result.json',
                 procCount=3):
        self._artifactsPath = artifactsPath
        self._buildResultPath = buildResultPath
        self._procCount = procCount

    def build(self, configsPath):
        if os.path.exists(self._artifactsPath):
            shutil.rmtree(self._artifactsPath) ##
        os.mkdir(self._artifactsPath)
        with open(configsPath) as f:
            configs = jstyleson.load(f)

        logs = self._runJobs(copy.deepcopy(configs['jobs']))
        self._processArtifacts(configs['jobs'])
        self._processGoals(configs['goals'], logs)

        with open(self._buildResultPath, 'w') as f:
            jstyleson.dump(logs, f, separators=(',', ': '), indent=4)

    def _runJobs(self, jobs):
        manager = mp.Manager()
        queue = mp.JoinableQueue()
        lock = mp.Lock()
        failureEvent = mp.Event()
        logs_shared = manager.dict({'state': 'sucsess', 'jobs': []})
        jobs_shared = manager.list()
        workers = []
        for _ in range(self._procCount):
            worker = mp.Process(target=self._runJob,
                                args=(logs_shared, queue, lock, failureEvent))
            worker.start()
            workers.append(worker)

        while jobs:
            independentJobs = [job for job in jobs if not job.get('depends_on')]
            for indJob in independentJobs:
                jobs.remove(indJob)
                queue.put(indJob)
            for indJob in independentJobs:
                for job in jobs:
                    depJobs = job.get('depends_on')
                    if depJobs and indJob['name'] in depJobs:
                        depJobs.remove(indJob['name'])

        for _ in workers:
            queue.put(None)
        for worker in workers:
            worker.join()
        logs = dict(logs_shared)
        manager.shutdown()
        if failureEvent.is_set():
            logs_shared['state'] = 'failure'
        return logs

    def _runJob(self, logs_shared, queue, lock, failureEvent):
        while not failureEvent.is_set():
            job = queue.get()
            if job is None:
                break
            cwd = os.path.join(self._artifactsPath, job['name'])
            os.mkdir(cwd)
            
            try:
                for command in job['commands']:
                    print('--', 'job: %-15s' % job['name'], command) ##
                    subprocess.check_call(shlex.split(command),
                                          timeout=job.get('timeout'),
                                          cwd=cwd,
                                          stderr=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                state = 'timeout'
                failureEvent.set()
            except subprocess.CalledProcessError:
                state = 'failure'
                failureEvent.set()
            else:
                state = 'sucsess'

            with lock:
                logs_shared['jobs'] += [{'name': job['name'],
                                        'state': state}]
            queue.task_done()

    def _processArtifacts(self, jobs):
        visited = set()
        for job in jobs:
            if job['name'] not in visited:
                self._dfs(job, jobs, visited, self._artifactsPath)

    def _dfs(self, curJob, jobs, visited, cwd):
        visited.add(curJob['name'])
        cwd = os.path.join(cwd, curJob['name'])
        oldPath = os.path.join(self._artifactsPath, curJob['name'])
        if os.path.exists(oldPath):
            os.rename(oldPath, cwd)
        for depJobName in curJob.get('depends_on', []):
            inRoot = os.path.exists(os.path.join(self._artifactsPath, depJobName))
            if depJobName not in visited or inRoot:
                cwd = os.path.join(cwd, 'input')
                if not os.path.exists(cwd):
                    os.mkdir(cwd)
                depJob = self._getJobByName(depJobName, jobs)
                self._dfs(depJob, jobs, visited, cwd)
                cwd = os.path.split(cwd)[0]

    def _processGoals(self, goals, logs):
        for root, dirs, _ in os.walk(self._artifactsPath):
            dirname = os.path.split(root)[1]
            if dirname in goals:
                newPath = os.path.join(self._artifactsPath, dirname)
                os.rename(root, newPath)
                dirs.clear()
        for dirname in os.listdir(self._artifactsPath):
            if dirname not in goals:
                dirpath = os.path.join(self._artifactsPath, dirname)
                shutil.rmtree(dirpath)
        for root, _, _ in os.walk(self._artifactsPath):
            dirname = os.path.split(root)[1]
            if dirname in goals:
                path = os.path.abspath(root)
                job = self._getJobByName(dirname, logs['jobs'])
                job['artifact'] = path

    def _getJobByName(self, name, jobs):
        for job in jobs:
            if job['name'] == name:
                return job
