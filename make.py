import sys
from src.builder import Builder


if len(sys.argv) < 2:
    print('The path to the configuration file is required')
else:
    builder = Builder()
    builder.build(sys.argv[1])
