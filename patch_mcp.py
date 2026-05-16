import inspect
import mcp.server.fastmcp.tools.base as b

lines = open(b.__file__).readlines()
patched = []
needs_import = True
for line in lines:
    if 'issubclass(param.annotation, Context)' in line and 'inspect.isclass' not in line:
        if needs_import:
            patched.append('import inspect\n')
            needs_import = False
        line = line.replace(
            'issubclass(param.annotation, Context)',
            'inspect.isclass(param.annotation) and issubclass(param.annotation, Context)'
        )
    patched.append(line)
open(b.__file__, 'w').writelines(patched)
print('FastMCP patched successfully')
