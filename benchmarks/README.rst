Benchmark Suite
===============

This directory contains ``py-test``-based benchmarks for Tahoe-LAFS.

These benchmarks are in an early state and improvements are welcome!

(Can we say more about the goals?)


Running the Benchmarks
----------------------

You must provide the number of "nodes" to run via the ``--number-of-nodes`` option (or else every test is skipped).

To get all dependencies, install tahoe with the ``test`` extra.
For example, from the top-level one might do: ``pip install --editable .[test]``

To run with 5 storage servers (aka "nodes")::

  py.test -sv  benchmarks/ --number-of-nodes 5

The ``-s`` option provides immediate output from fixtures and so forth and can be useful when analyzing problems.
The ``-v`` option is "verbose" and prints out longer test-names.

You can look for the string "BENCHMARK RESULT" for the results.

Additionally, a JSON representation of the benchmarks is written to ``--json-file`` (by default ``"tahoe-benchmarks.json"``).
Note that because this option is defined inside ``benchmarks/conftest.py`` you must use it only after the "``benchmarks/`" argument.
