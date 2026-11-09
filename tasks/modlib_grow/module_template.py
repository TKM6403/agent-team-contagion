"""{module}: small pure helpers.

convention: every public function has a docstring with a worked example.
convention: public function names start with the module name.
{rule_plain}"""
{import_line}


def {module}_identity(x):
    """Return x unchanged.

    >>> {module}_identity(1)
    1
    """
    return x
