"""Reference implementation of the Flow 1 intake pipeline (MVP, file-based, no database).

It exists so the intake test suite can run end to end. A team building the real MVP can keep
the tests and replace this package, as long as it exposes the same small interface
(see README: "Plugging in your own implementation").
"""
