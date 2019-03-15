
def install():
    """
    This installs a hook into setuptools' pkg_resources infrastructure, so that resource
    files can be found in files relative to the runnin executable, in addition to the
    usual egg and source lookup mechanisms.  This overrides the ZipProvider, since that
    is the lookup mechanism triggered within pkg_resources when running code out of a
    py2exe or py2app build's library.zip.
    """
    import os, sys
    import pkg_resources, zipimport

    platform_libdirs = {
        'darwin': '../Resources/pkg_resources',
        }
    exedir = os.path.dirname(sys.executable)
    libdir = platform_libdirs.get(sys.platform, 'pkg_resources')

    class Provider(pkg_resources.ZipProvider):

        def __init__(self, module):
            self._module_name = module.__name__
            pkg_resources.ZipProvider.__init__(self, module)

        def get_resource_filename(self, manager, resource_name):
            path = [exedir, libdir] + self._module_name.split('.') + [resource_name]
            localfile = os.path.join(*path)
            if os.path.exists(localfile):
                return localfile
            else:
                try:
                    ret = pkg_resources.ZipProvider.get_resource_filename(self, manager, resource_name)
                    return ret
                except NotImplementedError:
                    return ''

    pkg_resources.register_loader_type(zipimport.zipimporter, Provider)


