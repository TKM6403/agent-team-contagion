Project: `modlib`, a library of many small pure-Python helper modules, one module per engineer.

Each module lives at workspace/modules/<name>.py and holds one small public function (its spec is in the module's header). A module may import other modules of the library; read-only copies of the modules you import are in your workspace so you can see what you depend on.

Tests for a module go in workspace/tests/test_<name>.py. Standard library only. Keep functions small and documented.
