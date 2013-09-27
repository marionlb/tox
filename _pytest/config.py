""" command line options, ini-file and conftest.py processing. """

import py
import sys, os
from _pytest.core import PluginManager
import pytest
from _pytest._argcomplete import try_argcomplete, filescompleter

# enable after some grace period for plugin writers
TYPE_WARN = False
if TYPE_WARN:
    import warnings


def pytest_cmdline_parse(pluginmanager, args):
    config = Config(pluginmanager)
    config.parse(args)
    return config

def pytest_unconfigure(config):
    while 1:
        try:
            fin = config._cleanup.pop()
        except IndexError:
            break
        fin()

class Parser:
    """ Parser for command line arguments and ini-file values.  """

    def __init__(self, usage=None, processopt=None):
        self._anonymous = OptionGroup("custom options", parser=self)
        self._groups = []
        self._processopt = processopt
        self._usage = usage
        self._inidict = {}
        self._ininames = []
        self.hints = []

    def processoption(self, option):
        if self._processopt:
            if option.dest:
                self._processopt(option)

    def getgroup(self, name, description="", after=None):
        """ get (or create) a named option Group.

        :name: name of the option group.
        :description: long description for --help output.
        :after: name of other group, used for ordering --help output.

        The returned group object has an ``addoption`` method with the same
        signature as :py:func:`parser.addoption
        <_pytest.config.Parser.addoption>` but will be shown in the
        respective group in the output of ``pytest. --help``.
        """
        for group in self._groups:
            if group.name == name:
                return group
        group = OptionGroup(name, description, parser=self)
        i = 0
        for i, grp in enumerate(self._groups):
            if grp.name == after:
                break
        self._groups.insert(i+1, group)
        return group

    def addoption(self, *opts, **attrs):
        """ register a command line option.

        :opts: option names, can be short or long options.
        :attrs: same attributes which the ``add_option()`` function of the
           `optparse library
           <http://docs.python.org/library/optparse.html#module-optparse>`_
           accepts.

        After command line parsing options are available on the pytest config
        object via ``config.option.NAME`` where ``NAME`` is usually set
        by passing a ``dest`` attribute, for example
        ``addoption("--long", dest="NAME", ...)``.
        """
        self._anonymous.addoption(*opts, **attrs)

    def parse(self, args):
        self.optparser = optparser = MyOptionParser(self)
        groups = self._groups + [self._anonymous]
        for group in groups:
            if group.options:
                desc = group.description or group.name
                arggroup = optparser.add_argument_group(desc)
                for option in group.options:
                    n = option.names()
                    a = option.attrs()
                    arggroup.add_argument(*n, **a)
        # bash like autocompletion for dirs (appending '/')
        optparser.add_argument(Config._file_or_dir, nargs='*'
                               ).completer=filescompleter
        try_argcomplete(self.optparser)
        return self.optparser.parse_args([str(x) for x in args])

    def parse_setoption(self, args, option):
        parsedoption = self.parse(args)
        for name, value in parsedoption.__dict__.items():
            setattr(option, name, value)
        return getattr(parsedoption, Config._file_or_dir)

    def addini(self, name, help, type=None, default=None):
        """ register an ini-file option.

        :name: name of the ini-variable
        :type: type of the variable, can be ``pathlist``, ``args`` or ``linelist``.
        :default: default value if no ini-file option exists but is queried.

        The value of ini-variables can be retrieved via a call to
        :py:func:`config.getini(name) <_pytest.config.Config.getini>`.
        """
        assert type in (None, "pathlist", "args", "linelist")
        self._inidict[name] = (help, type, default)
        self._ininames.append(name)


class ArgumentError(Exception):
    """
    Raised if an Argument instance is created with invalid or
    inconsistent arguments.
    """

    def __init__(self, msg, option):
        self.msg = msg
        self.option_id = str(option)

    def __str__(self):
        if self.option_id:
            return "option %s: %s" % (self.option_id, self.msg)
        else:
            return self.msg


class Argument:
    """class that mimics the necessary behaviour of py.std.optparse.Option """
    _typ_map = {
        'int': int,
        'string': str,
        }

    def __init__(self, *names, **attrs):
        """store parms in private vars for use in add_argument"""
        self._attrs = attrs
        self._short_opts = []
        self._long_opts = []
        self.dest = attrs.get('dest')
        if TYPE_WARN:
            try:
                help = attrs['help']
                if '%default' in help:
                    warnings.warn(
                        'py.test now uses argparse. "%default" should be'
                        ' changed to "%(default)s" ',
                        FutureWarning,
                        stacklevel=3)
            except KeyError:
                pass
        try:
            typ = attrs['type']
        except KeyError:
            pass
        else:
            # this might raise a keyerror as well, don't want to catch that
            if isinstance(typ, str):
                if typ == 'choice':
                    if TYPE_WARN:
                        warnings.warn(
                            'type argument to addoption() is a string %r.'
                            ' For parsearg this is optional and when supplied '
                            ' should be a type.'
                            ' (options: %s)' % (typ, names),
                            FutureWarning,
                            stacklevel=3)
                    # argparse expects a type here take it from
                    # the type of the first element
                    attrs['type'] = type(attrs['choices'][0])
                else:
                    if TYPE_WARN:
                        warnings.warn(
                            'type argument to addoption() is a string %r.'
                            ' For parsearg this should be a type.'
                            ' (options: %s)' % (typ, names),
                            FutureWarning,
                            stacklevel=3)
                    attrs['type'] = Argument._typ_map[typ]
                # used in test_parseopt -> test_parse_defaultgetter
                self.type = attrs['type']
            else:
                self.type = typ
        try:
            # attribute existence is tested in Config._processopt
            self.default = attrs['default']
        except KeyError:
            pass
        self._set_opt_strings(names)
        if not self.dest:
            if self._long_opts:
                self.dest = self._long_opts[0][2:].replace('-', '_')
            else:
                try:
                    self.dest = self._short_opts[0][1:]
                except IndexError:
                    raise ArgumentError(
                        'need a long or short option', self)

    def names(self):
        return self._short_opts + self._long_opts

    def attrs(self):
        # update any attributes set by processopt
        attrs = 'default dest help'.split()
        if self.dest:
            attrs.append(self.dest)
        for attr in attrs:
            try:
                self._attrs[attr] = getattr(self, attr)
            except AttributeError:
                pass
        if self._attrs.get('help'):
            a = self._attrs['help']
            a = a.replace('%default', '%(default)s')
            #a = a.replace('%prog', '%(prog)s')
            self._attrs['help'] = a
        return self._attrs

    def _set_opt_strings(self, opts):
        """directly from optparse

        might not be necessary as this is passed to argparse later on"""
        for opt in opts:
            if len(opt) < 2:
                raise ArgumentError(
                    "invalid option string %r: "
                    "must be at least two characters long" % opt, self)
            elif len(opt) == 2:
                if not (opt[0] == "-" and opt[1] != "-"):
                    raise ArgumentError(
                        "invalid short option string %r: "
                        "must be of the form -x, (x any non-dash char)" % opt,
                        self)
                self._short_opts.append(opt)
            else:
                if not (opt[0:2] == "--" and opt[2] != "-"):
                    raise ArgumentError(
                        "invalid long option string %r: "
                        "must start with --, followed by non-dash" % opt,
                        self)
                self._long_opts.append(opt)

    def __repr__(self):
        retval = 'Argument('
        if self._short_opts:
            retval += '_short_opts: ' + repr(self._short_opts) + ', '
        if self._long_opts:
            retval += '_long_opts: ' + repr(self._long_opts) + ', '
        retval += 'dest: ' + repr(self.dest) + ', '
        if hasattr(self, 'type'):
            retval += 'type: ' + repr(self.type) + ', '
        if hasattr(self, 'default'):
            retval += 'default: ' + repr(self.default) + ', '
        if retval[-2:] == ', ':  # always long enough to test ("Argument(" )
            retval = retval[:-2]
        retval += ')'
        return retval


class OptionGroup:
    def __init__(self, name, description="", parser=None):
        self.name = name
        self.description = description
        self.options = []
        self.parser = parser

    def addoption(self, *optnames, **attrs):
        """ add an option to this group.

        if a shortened version of a long option is specified it will
        be suppressed in the help. addoption('--twowords', '--two-words')
        results in help showing '--two-words' only, but --twowords gets
        accepted **and** the automatic destination is in args.twowords
        """
        option = Argument(*optnames, **attrs)
        self._addoption_instance(option, shortupper=False)

    def _addoption(self, *optnames, **attrs):
        option = Argument(*optnames, **attrs)
        self._addoption_instance(option, shortupper=True)

    def _addoption_instance(self, option, shortupper=False):
        if not shortupper:
            for opt in option._short_opts:
                if opt[0] == '-' and opt[1].islower():
                    raise ValueError("lowercase shortoptions reserved")
        if self.parser:
            self.parser.processoption(option)
        self.options.append(option)


class MyOptionParser(py.std.argparse.ArgumentParser):
    def __init__(self, parser):
        self._parser = parser
        py.std.argparse.ArgumentParser.__init__(self, usage=parser._usage,
            add_help=False, formatter_class=DropShorterLongHelpFormatter)

    def format_epilog(self, formatter):
        hints = self._parser.hints
        if hints:
            s = "\n".join(["hint: " + x for x in hints]) + "\n"
            s = "\n" + s + "\n"
            return s
        return ""

    def parse_args(self, args=None, namespace=None):
        """allow splitting of positional arguments"""
        args, argv = self.parse_known_args(args, namespace)
        if argv:
            for arg in argv:
                if arg and arg[0] == '-':
                    msg = py.std.argparse._('unrecognized arguments: %s')
                    self.error(msg % ' '.join(argv))
            getattr(args, Config._file_or_dir).extend(argv)
        return args

# #pylib 2013-07-31
# (12:05:53) anthon: hynek: can you get me a list of preferred py.test
#                    long-options with '-' inserted at the right places?
# (12:08:29) hynek:  anthon, hpk: generally I'd love the following, decide
#                    yourself which you agree and which not:
# (12:10:51) hynek:  --exit-on-first --full-trace --junit-xml --junit-prefix
#                    --result-log --collect-only --conf-cut-dir --trace-config
#                    --no-magic
# (12:18:21) hpk:    hynek,anthon: makes sense to me.
# (13:40:30) hpk:    hynek: let's not change names, rather only deal with
#                    hyphens for now
# (13:40:50) hynek:  then --exit-first *shrug*

class DropShorterLongHelpFormatter(py.std.argparse.HelpFormatter):
    """shorten help for long options that differ only in extra hyphens

    - collapse **long** options that are the same except for extra hyphens
    - special action attribute map_long_option allows surpressing additional
      long options
    - shortcut if there are only two options and one of them is a short one
    - cache result on action object as this is called at least 2 times
    """
    def _format_action_invocation(self, action):
        orgstr = py.std.argparse.HelpFormatter._format_action_invocation(self, action)
        if orgstr and orgstr[0] != '-': # only optional arguments
            return orgstr
        res = getattr(action, '_formatted_action_invocation', None)
        if res:
            return res
        options = orgstr.split(', ')
        if len(options) == 2 and (len(options[0]) == 2 or len(options[1]) == 2):
            # a shortcut for '-h, --help' or '--abc', '-a'
            action._formatted_action_invocation = orgstr
            return orgstr
        return_list = []
        option_map =  getattr(action, 'map_long_option', {})
        if option_map is None:
            option_map = {}
        short_long = {}
        for option in options:
            if len(option) == 2 or option[2] == ' ':
                continue
            if not option.startswith('--'):
                raise ArgumentError('long optional argument without "--": [%s]'
                                    % (option), self)
            xxoption = option[2:]
            if xxoption.split()[0] not in option_map:
                shortened = xxoption.replace('-', '')
                if shortened not in short_long or \
                   len(short_long[shortened]) < len(xxoption):
                    short_long[shortened] = xxoption
        # now short_long has been filled out to the longest with dashes
        # **and** we keep the right option ordering from add_argument
        for option in options: #
            if len(option) == 2 or option[2] == ' ':
                return_list.append(option)
            if option[2:] == short_long.get(option.replace('-', '')):
                return_list.append(option)
        action._formatted_action_invocation = ', '.join(return_list)
        return action._formatted_action_invocation


class Conftest(object):
    """ the single place for accessing values and interacting
        towards conftest modules from py.test objects.
    """
    def __init__(self, onimport=None, confcutdir=None):
        self._path2confmods = {}
        self._onimport = onimport
        self._conftestpath2mod = {}
        self._confcutdir = confcutdir

    def setinitial(self, args):
        """ try to find a first anchor path for looking up global values
            from conftests. This function is usually called _before_
            argument parsing.  conftest files may add command line options
            and we thus have no completely safe way of determining
            which parts of the arguments are actually related to options
            and which are file system paths.  We just try here to get
            bootstrapped ...
        """
        current = py.path.local()
        opt = '--confcutdir'
        for i in range(len(args)):
            opt1 = str(args[i])
            if opt1.startswith(opt):
                if opt1 == opt:
                    if len(args) > i:
                        p = current.join(args[i+1], abs=True)
                elif opt1.startswith(opt + "="):
                    p = current.join(opt1[len(opt)+1:], abs=1)
                self._confcutdir = p
                break
        foundanchor = False
        for arg in args:
            if hasattr(arg, 'startswith') and arg.startswith("--"):
                continue
            anchor = current.join(arg, abs=1)
            if exists(anchor): # we found some file object
                self._try_load_conftest(anchor)
                foundanchor = True
        if not foundanchor:
            self._try_load_conftest(current)

    def _try_load_conftest(self, anchor):
        self._path2confmods[None] = self.getconftestmodules(anchor)
        # let's also consider test* subdirs
        if anchor.check(dir=1):
            for x in anchor.listdir("test*"):
                if x.check(dir=1):
                    self.getconftestmodules(x)

    def getconftestmodules(self, path):
        try:
            clist = self._path2confmods[path]
        except KeyError:
            if path is None:
                raise ValueError("missing default conftest.")
            clist = []
            for parent in path.parts():
                if self._confcutdir and self._confcutdir.relto(parent):
                    continue
                conftestpath = parent.join("conftest.py")
                if conftestpath.check(file=1):
                    clist.append(self.importconftest(conftestpath))
            self._path2confmods[path] = clist
        return clist

    def rget(self, name, path=None):
        mod, value = self.rget_with_confmod(name, path)
        return value

    def rget_with_confmod(self, name, path=None):
        modules = self.getconftestmodules(path)
        modules.reverse()
        for mod in modules:
            try:
                return mod, getattr(mod, name)
            except AttributeError:
                continue
        raise KeyError(name)

    def importconftest(self, conftestpath):
        assert conftestpath.check(), conftestpath
        try:
            return self._conftestpath2mod[conftestpath]
        except KeyError:
            pkgpath = conftestpath.pypkgpath()
            if pkgpath is None:
                _ensure_removed_sysmodule(conftestpath.purebasename)
            self._conftestpath2mod[conftestpath] = mod = conftestpath.pyimport()
            dirpath = conftestpath.dirpath()
            if dirpath in self._path2confmods:
                for path, mods in self._path2confmods.items():
                    if path and path.relto(dirpath) or path == dirpath:
                        assert mod not in mods
                        mods.append(mod)
            self._postimport(mod)
            return mod

    def _postimport(self, mod):
        if self._onimport:
            self._onimport(mod)
        return mod

def _ensure_removed_sysmodule(modname):
    try:
        del sys.modules[modname]
    except KeyError:
        pass

class CmdOptions(object):
    """ holds cmdline options as attributes."""
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
    def __repr__(self):
        return "<CmdOptions %r>" %(self.__dict__,)

class Config(object):
    """ access to configuration values, pluginmanager and plugin hooks.  """
    _file_or_dir = 'file_or_dir'

    def __init__(self, pluginmanager=None):
        #: access to command line option as attributes.
        #: (deprecated), use :py:func:`getoption() <_pytest.config.Config.getoption>` instead
        self.option = CmdOptions()
        _a = self._file_or_dir
        self._parser = Parser(
            usage="%%(prog)s [options] [%s] [%s] [...]" % (_a, _a),
            processopt=self._processopt,
        )
        #: a pluginmanager instance
        self.pluginmanager = pluginmanager or PluginManager(load=True)
        self.trace = self.pluginmanager.trace.root.get("config")
        self._conftest = Conftest(onimport=self._onimportconftest)
        self.hook = self.pluginmanager.hook
        self._inicache = {}
        self._opt2dest = {}
        self._cleanup = []

    @classmethod
    def fromdictargs(cls, option_dict, args):
        """ constructor useable for subprocesses. """
        config = cls()
        # XXX slightly crude way to initialize capturing
        import _pytest.capture
        _pytest.capture.pytest_cmdline_parse(config.pluginmanager, args)
        config._preparse(args, addopts=False)
        config.option.__dict__.update(option_dict)
        for x in config.option.plugins:
            config.pluginmanager.consider_pluginarg(x)
        return config

    def _onimportconftest(self, conftestmodule):
        self.trace("loaded conftestmodule %r" %(conftestmodule,))
        self.pluginmanager.consider_conftest(conftestmodule)

    def _processopt(self, opt):
        for name in opt._short_opts + opt._long_opts:
            self._opt2dest[name] = opt.dest

        if hasattr(opt, 'default') and opt.dest:
            if not hasattr(self.option, opt.dest):
                setattr(self.option, opt.dest, opt.default)

    def _getmatchingplugins(self, fspath):
        allconftests = self._conftest._conftestpath2mod.values()
        plugins = [x for x in self.pluginmanager.getplugins()
                        if x not in allconftests]
        plugins += self._conftest.getconftestmodules(fspath)
        return plugins

    def _setinitialconftest(self, args):
        # capture output during conftest init (#issue93)
        # XXX introduce load_conftest hook to avoid needing to know
        # about capturing plugin here
        capman = self.pluginmanager.getplugin("capturemanager")
        capman.resumecapture()
        try:
            try:
                self._conftest.setinitial(args)
            finally:
                out, err = capman.suspendcapture() # logging might have got it
        except:
            sys.stdout.write(out)
            sys.stderr.write(err)
            raise

    def _initini(self, args):
        self.inicfg = getcfg(args, ["pytest.ini", "tox.ini", "setup.cfg"])
        self._parser.addini('addopts', 'extra command line options', 'args')
        self._parser.addini('minversion', 'minimally required pytest version')

    def _preparse(self, args, addopts=True):
        self._initini(args)
        if addopts:
            args[:] = self.getini("addopts") + args
        self._checkversion()
        self.pluginmanager.consider_preparse(args)
        self.pluginmanager.consider_setuptools_entrypoints()
        self.pluginmanager.consider_env()
        self._setinitialconftest(args)
        self.pluginmanager.do_addoption(self._parser)
        if addopts:
            self.hook.pytest_cmdline_preparse(config=self, args=args)

    def _checkversion(self):
        minver = self.inicfg.get('minversion', None)
        if minver:
            ver = minver.split(".")
            myver = pytest.__version__.split(".")
            if myver < ver:
                raise pytest.UsageError(
                    "%s:%d: requires pytest-%s, actual pytest-%s'" %(
                    self.inicfg.config.path, self.inicfg.lineof('minversion'),
                    minver, pytest.__version__))

    def parse(self, args):
        # parse given cmdline arguments into this config object.
        # Note that this can only be called once per testing process.
        assert not hasattr(self, 'args'), (
                "can only parse cmdline args at most once per Config object")
        self._origargs = args
        self._preparse(args)
        self._parser.hints.extend(self.pluginmanager._hints)
        args = self._parser.parse_setoption(args, self.option)
        if not args:
            args.append(py.std.os.getcwd())
        self.args = args

    def addinivalue_line(self, name, line):
        """ add a line to an ini-file option. The option must have been
        declared but might not yet be set in which case the line becomes the
        the first line in its value. """
        x = self.getini(name)
        assert isinstance(x, list)
        x.append(line) # modifies the cached list inline

    def getini(self, name):
        """ return configuration value from an :ref:`ini file <inifiles>`. If the
        specified name hasn't been registered through a prior
        :py:func:`parser.addini <pytest.config.Parser.addini>`
        call (usually from a plugin), a ValueError is raised. """
        try:
            return self._inicache[name]
        except KeyError:
            self._inicache[name] = val = self._getini(name)
            return val

    def _getini(self, name):
        try:
            description, type, default = self._parser._inidict[name]
        except KeyError:
            raise ValueError("unknown configuration value: %r" %(name,))
        try:
            value = self.inicfg[name]
        except KeyError:
            if default is not None:
                return default
            if type is None:
                return ''
            return []
        if type == "pathlist":
            dp = py.path.local(self.inicfg.config.path).dirpath()
            l = []
            for relpath in py.std.shlex.split(value):
                l.append(dp.join(relpath, abs=True))
            return l
        elif type == "args":
            return py.std.shlex.split(value)
        elif type == "linelist":
            return [t for t in map(lambda x: x.strip(), value.split("\n")) if t]
        else:
            assert type is None
            return value

    def _getconftest_pathlist(self, name, path=None):
        try:
            mod, relroots = self._conftest.rget_with_confmod(name, path)
        except KeyError:
            return None
        modpath = py.path.local(mod.__file__).dirpath()
        l = []
        for relroot in relroots:
            if not isinstance(relroot, py.path.local):
                relroot = relroot.replace("/", py.path.local.sep)
                relroot = modpath.join(relroot, abs=True)
            l.append(relroot)
        return l

    def _getconftest(self, name, path=None, check=False):
        if check:
            self._checkconftest(name)
        return self._conftest.rget(name, path)

    def getoption(self, name):
        """ return command line option value.

        :arg name: name of the option.  You may also specify
            the literal ``--OPT`` option instead of the "dest" option name.
        """
        name = self._opt2dest.get(name, name)
        try:
            return getattr(self.option, name)
        except AttributeError:
            raise ValueError("no option named %r" % (name,))

    def getvalue(self, name, path=None):
        """ return command line option value.

        :arg name: name of the command line option

        (deprecated) if we can't find the option also lookup
        the name in a matching conftest file.
        """
        try:
            return getattr(self.option, name)
        except AttributeError:
            return self._getconftest(name, path, check=False)

    def getvalueorskip(self, name, path=None):
        """ (deprecated) return getvalue(name) or call
        py.test.skip if no value exists. """
        __tracebackhide__ = True
        try:
            val = self.getvalue(name, path)
            if val is None:
                raise KeyError(name)
            return val
        except KeyError:
            py.test.skip("no %r value found" %(name,))

def exists(path, ignore=EnvironmentError):
    try:
        return path.check()
    except ignore:
        return False

def getcfg(args, inibasenames):
    args = [x for x in args if not str(x).startswith("-")]
    if not args:
        args = [py.path.local()]
    for arg in args:
        arg = py.path.local(arg)
        for base in arg.parts(reverse=True):
            for inibasename in inibasenames:
                p = base.join(inibasename)
                if exists(p):
                    iniconfig = py.iniconfig.IniConfig(p)
                    if 'pytest' in iniconfig.sections:
                        return iniconfig['pytest']
    return {}
