Project: `tinyutils`, a small pure-Python utility library.

Planned modules, one file each under workspace/tinyutils/:
- strings.py: slugify(text), truncate(text, n)
- dates.py: parse_iso(s), days_between(a, b)
- files.py: read_lines(path), safe_name(name)
- mathx.py: clamp(x, lo, hi), mean(values)
- text.py: word_count(text), dedent_lines(lines)

Tests go in workspace/tests/test_<module>.py. Docs go in workspace/docs/<module>.md.
Standard library only. Keep functions small and documented.
