"""Adapters. The only layer allowed to import `mcp`.

  cli         JSON CLI, consumed by Obsidian
  read_mcp    the read tools
  write_mcp   the gated write tools

`mcp` is imported INSIDE `main()`, never at module scope, so that importing a verb
does not require an async runtime -- and so `write/transports/cookjohn.py` stays
stdlib-only and vendorable into Calibre's embedded Python, which cannot see a uv
virtualenv. Two contracts and two AST-level tests hold that line.
"""
