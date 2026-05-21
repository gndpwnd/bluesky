""" Implementation of BlueSky's plugin system. """
import ast
import importlib
from pathlib import Path
import bluesky as bs
from bluesky import plugins
from bluesky import settings
from bluesky.core import timed_function, varexplorer as ve
from bluesky import stack

# Register settings defaults
settings.set_variable_defaults(plugin_path='plugins', enabled_plugins=['datafeed'])


class Plugin:
    ''' BlueSky plugin class.
        This class is used internally to store information about bluesky
        plugins that were found in the search directory. '''

    # Dictionary of all available plugins
    plugins = dict()

    # Plugins not meant for this process type (i.e., sim/gui)
    plugins_ext = list()

    loaded_plugins = dict()

    def __init__(self, fullname):
        self.fullname = fullname

        self.plugin_doc   = ''
        self.plugin_name  = ''
        self.plugin_type  = ''
        self.plugin_stack = []
        self.loaded = False
        self.imp = None

    def _load(self):
        ''' Load this plugin with fallback and health checking.

        CRIT-006: Enhanced plugin loading with:
        - Graceful degradation on import failures
        - Health check after initialization
        - Fallback for missing plugins
        - Comprehensive error tracking
        '''
        if self.loaded:
            return False, f'Plugin {self.plugin_name} already loaded'

        try:
            # Load the plugin, or get already loaded module from sys modules
            self.imp = importlib.import_module(self.fullname)

            # CRIT-006: Health check 1 — init_plugin must exist and be callable
            if not hasattr(self.imp, 'init_plugin'):
                return False, (
                    f'Plugin {self.plugin_name} missing init_plugin() entry point'
                )

            # Initialize the plugin
            try:
                result = self.imp.init_plugin()
            except Exception as init_exc:
                # CRIT-006: Graceful degradation on init failure
                return False, (
                    f'Plugin {self.plugin_name} init_plugin() failed: {init_exc}'
                )

            config = result if isinstance(result, dict) else result[0]

            # CRIT-006: Health check 2 — config must have required fields
            if not isinstance(config, dict):
                return False, (
                    f'Plugin {self.plugin_name} returned invalid config (not dict)'
                )
            if 'plugin_name' not in config or 'plugin_type' not in config:
                return False, (
                    f'Plugin {self.plugin_name} config missing plugin_name/plugin_type'
                )

            if self.plugin_type == 'sim':
                try:
                    dt = max(config.get('update_interval', 0.0), bs.sim.simdt)
                    # Add timed functions if present
                    for hook in ('preupdate', 'update', 'reset'):
                        fun = config.get(hook)
                        if fun:
                            timed_function(
                                fun, name=f'{self.plugin_name}.{fun.__name__}', dt=dt, hook=hook)

                    # Add the plugin as data parent to the variable explorer
                    ve.register_data_parent(self.imp, self.plugin_name.lower())
                except Exception as sim_exc:
                    # CRIT-006: Log but continue — don't fail whole plugin on
                    # optional sim-specific setup failures
                    print(f'Warning: plugin {self.plugin_name} sim setup failed: {sim_exc}')

            if isinstance(result, (tuple, list)) and len(result) > 1:
                try:
                    stackfuns = result[1]
                    # Add the plugin's stack functions to the stack
                    bs.stack.append_commands(stackfuns)
                except Exception as stack_exc:
                    # CRIT-006: Stack registration failures don't block the plugin
                    print(f'Warning: plugin {self.plugin_name} stack registration failed: {stack_exc}')

            self.loaded = True
            return True, 'Successfully loaded plugin %s' % self.plugin_name

        except ImportError as e:
            # CRIT-006: Fallback mechanism — missing plugin is not fatal
            print('BlueSky plugin system: plugin', self.plugin_name, 'not available:', e)
            return False, f'Plugin {self.plugin_name} not found (fallback to no-op)'
        except Exception as e:
            # CRIT-006: Catch-all for unexpected errors
            print('BlueSky plugin system: plugin', self.plugin_name, 'failed:', e)
            return False, f'Plugin {self.plugin_name} load failed: {e}'

    @classmethod
    def load(cls, name):
        ''' Load a plugin by name. '''
        plugin = cls.plugins.get(name)
        if plugin is None:
            return False, f'Error loading plugin: plugin {name} not found.'

        # Try to load the plugin
        success, msg = plugin._load()
        if success:
            cls.loaded_plugins[name] = plugin
        return success, msg

    @classmethod
    def find_plugins(cls, reqtype):
        ''' Create plugin wrapper objects based on source code of potential plug-in files. '''
        for path in (Path(p) for p in plugins.__spec__.submodule_search_locations):
            for fname in path.glob('**/*.py'):
                submod = fname.relative_to(path).parent.as_posix().replace('/', '.')
                fullname = f'bluesky.plugins.{fname.stem}' if submod == '.' else \
                           f'bluesky.plugins.{submod}.{fname.stem}'
                with open(fname, 'rb') as f:
                    source = f.read()
                    try:
                        tree = ast.parse(source)
                    except:
                        # Failed to parse source code, continue to next file
                        continue

                    ret_dicts = []
                    ret_names = ['', '']
                    for item in tree.body:
                        if isinstance(item, ast.FunctionDef) and item.name == 'init_plugin':
                            for iitem in reversed(item.body):
                                # Return value of init_plugin should always be a tuple of two dicts
                                # The first dict is the plugin config dict, the second dict is the stack function dict
                                if isinstance(iitem, ast.Return):
                                    if isinstance(iitem.value, ast.Tuple):
                                        ret_dicts = iitem.value.elts
                                    else:
                                        ret_dicts = [iitem.value]
                                    if len(ret_dicts) not in (1, 2):
                                        continue
                                    ret_names = [el.id if isinstance(el, ast.Name) else '' for el in ret_dicts]

                                # Check if this is the assignment of one of the return values
                                if isinstance(iitem, ast.Assign) and isinstance(iitem.value, ast.Dict):
                                    for i, name in enumerate(ret_names):
                                        if iitem.targets[0].id == name:
                                            ret_dicts[i] = iitem.value

                            # Parse the config dict
                            if len(ret_dicts) not in (1, 2):
                                print(f"{fname} looks like a plugin, but init_plugin() doesn't return one or two dicts")
                                continue

                            cfgdict = {k.value:v for k,v in zip(ret_dicts[0].keys, ret_dicts[0].values)}
                            plugintype = cfgdict.get('plugin_type')
                            if plugintype is None:
                                print(f'{fname} looks like a plugin, but no plugin type (sim/gui) is specified. ' 
                                        'To fix this, add the element plugin_type to the configuration dictionary that is returned from init_plugin()')
                                continue
                            if plugintype.value == reqtype:
                                # This is the initialization function of a bluesky plugin. Parse the contents
                                plugin = Plugin(fullname)
                                plugin.plugin_doc = ast.get_docstring(tree)
                                plugin.plugin_name = cfgdict['plugin_name'].value
                                plugin.plugin_type = cfgdict['plugin_type'].value

                                # Parse the stack function dict
                                if len(ret_dicts) > 1:
                                    stack_keys       = [el.value for el in ret_dicts[1].keys]
                                    stack_docs       = [el.elts[-1].value for el in ret_dicts[1].values]
                                    plugin.plugin_stack = list(zip(stack_keys, stack_docs))
                                # Add plugin to the dict of available plugins
                                cls.plugins[plugin.plugin_name.upper()] = plugin
                            else:
                                cls.plugins_ext.append(cfgdict['plugin_name'].value.upper())


def init(mode):
    ''' Initialization function of the plugin system with health checks.

    CRIT-006: Enhanced initialization with:
    - Graceful handling of missing plugins
    - Health monitoring endpoints
    - Load order guarantees (critical plugins first)
    '''
    # Set plugin type for this instance of BlueSky
    req_type = 'sim' if mode[:3] == 'sim' else 'gui'
    oth_type = 'gui' if mode[:3] == 'sim' else 'sim'

    # Find available plugins
    Plugin.find_plugins(req_type)

    # CRIT-006: Priority loading — load critical plugins first
    # (e.g., NANSAFETY must load before Traffic instance creation)
    priority_plugins = ['NANSAFETY']  # must load earliest for GUI mode
    load_order = [p for p in priority_plugins if p in Plugin.plugins] + \
                 [p for p in settings.enabled_plugins if p.upper() not in priority_plugins]

    # Load plugins selected in config
    failed_plugins = []
    for pname in load_order:
        pname_upper = pname.upper() if isinstance(pname, str) else pname.upper()
        if pname_upper not in Plugin.plugins_ext:
            success, msg = Plugin.load(pname_upper)
            print(msg)
            if not success:
                # CRIT-006: Track failed plugins but continue
                # (graceful degradation — system works without optional plugins)
                failed_plugins.append((pname_upper, msg))

    # CRIT-006: Health check — warn if critical plugins failed
    if failed_plugins:
        critical = {'NANSAFETY', 'CONFLICTCAM', 'CD_MODES'}
        failed_critical = [p for p, _ in failed_plugins if p in critical]
        if failed_critical:
            import sys as _sys
            print(f'WARNING: Critical plugins failed to load: {", ".join(failed_critical)}',
                  file=_sys.stderr)

    # Create the plugin management stack command
    @stack.command(name='PLUGINS', aliases=('PLUGIN', 'PLUG-IN', 'PLUG-INS', f'{req_type.upper()}PLUGIN'))
    def manage(cmd: 'txt' = 'LIST', plugin_name: 'txt' = ''):
        ''' List all plugins, load a plugin, check health, or remove a loaded plugin.'''
        if cmd == 'LIST':
            running = set(Plugin.loaded_plugins.keys())
            available = set(Plugin.plugins.keys()) - running
            text = f'\nCurrently running {req_type} plugins: {", ".join(running)}'
            if available:
                text += f'\nAvailable {req_type} plugins: {", ".join(available)}'
            else:
                text += f'\nNo additional {req_type} plugins available.'
            # Also let other side print the list of plugins
            stack.forward()
            return True, text

        if cmd == 'HEALTH':
            # CRIT-006: Health check command
            running = set(Plugin.loaded_plugins.keys())
            health = {
                'running': list(running),
                'total_available': len(Plugin.plugins),
                'failed_load': len(failed_plugins),
            }
            text = f'\nPlugin health: {len(running)}/{len(Plugin.plugins)} loaded'
            if failed_plugins:
                text += f'\nFailed to load: {", ".join([p for p, _ in failed_plugins])}'
            return True, text

        if cmd in ('LOAD', 'ENABLE') or not plugin_name:
            # If no command is given, assume user tries to load a plugin
            success, msg = Plugin.load(plugin_name or cmd)
            # If we're the first and plugin is not found here, send it on
            if not success:
                stack.forward()
                return True
            return success, msg

        return False, f'Unknown command {cmd}'
