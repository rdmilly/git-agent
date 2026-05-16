"""Patch FastMCP base.py to fix issubclass bug with Python 3.12 annotations."""
import inspect
import mcp.server.fastmcp.tools.base as b

content = open(b.__file__).read()

if 'inspect.isclass(param.annotation)' not in content:
    # Only patch the specific line, preserving indentation
    content = content.replace(
        'if issubclass(param.annotation, Context):',
        'if inspect.isclass(param.annotation) and issubclass(param.annotation, Context):'
    )
    # Ensure inspect is imported at the top
    if 'import inspect' not in content:
        content = 'import inspect\n' + content
    open(b.__file__, 'w').write(content)
    print(f'Patched {b.__file__}')
else:
    print('Already patched')
