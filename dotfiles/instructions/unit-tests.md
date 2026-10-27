## Unit tests: behavior only, P0 only

Test the promise, not the plumbing.

- Input in, output and visible side effects out. Never assert that a private helper ran, or how many times. A test that breaks on a rename is testing the code, not the behavior.
- P0 only: the paths where a wrong answer loses data, corrupts state, or leaves the main flow dead. Skip getters, trivial branches, shape checks.
- One behavior per test. Three asserts to make one point is three cases.
- Every test must be able to fail. If deleting the feature still passes, it's decoration — delete it.

Coverage is not the goal. The goal is that the main flow can't rot without someone noticing.
