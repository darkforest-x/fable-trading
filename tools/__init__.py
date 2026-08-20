"""Repository tooling: consolidation, review, dataset and ops helpers.

A real package rather than an implicit namespace package, and the difference is
load-bearing. 35 scripts under scripts/ still insert ~/yoyo-trading onto
sys.path at import time, a leftover from the period when the yoyo package lived
in another repository. yoyo-trading also has a tools/ directory, and it has an
__init__.py.

Under implicit namespace rules the interpreter keeps scanning sys.path past a
directory with no __init__.py, collecting portions, and stops at the first
*regular* package it meets. So `tools` resolved to yoyo-trading's copy, and
`import tools.review` failed with "No module named 'tools.review'" -- but only
in a full test session, only after one of those 35 scripts had been imported,
and never when the test ran alone.

An __init__.py here ends the scan at this repository, whatever else is on the
path. The sys.path bridges are removed separately; this makes the resolution
deterministic in the meantime, and correct afterwards.
"""
