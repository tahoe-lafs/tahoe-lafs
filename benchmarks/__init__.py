"""
pytest-based end-to-end benchmarks of Tahoe-LAFS.

Usage:

$ systemd-run --user --scope pytest benchmark --number-of-nodes=3

It's possible to pass --number-of-nodes multiple times.

The systemd-run makes sure the tests run in their own cgroup so we get CPU
accounting correct.
"""
