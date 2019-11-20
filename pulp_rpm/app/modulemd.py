import json
import os
import tempfile

from pulpcore.app.models.content import Artifact
from pulp_rpm.app.models import Modulemd, Package
from pulp_rpm.app.constants import PULP_MODULE_ATTR, PULP_MODULEDEFAULTS_ATTR

import gi
gi.require_version('Modulemd', '2.0')
from gi.repository import Modulemd as mmdlib  # noqa: E402


def resolve_module_packages(version, previous_version):
    """
    Decide which packages to add/remove based on modular data.

    Args:
        version (pulpcore.app.models.RepositoryVersion): current incomplete repository version
    """
    def modules_packages(modules):
        packages = set()
        for module in modules:
            packages.update(module.packages.all())
        return packages

    current_modules = Modulemd.objects \
        .filter(pk__in=version.content.filter(pulp_type="rpm.modulemd"))

    if previous_version:
        previous_modules = Modulemd.objects \
            .filter(pk__in=previous_version.content.filter(pulp_type="rpm.modulemd"))
        added_modules = current_modules.difference(previous_modules)
        removed_modules = previous_modules.difference(current_modules)
        current_module_packages = modules_packages(current_modules)
        removed_module_packages = modules_packages(removed_modules)
        packages_to_remove = removed_module_packages.difference(current_module_packages)
        version.remove_content(Package.objects.filter(pk__in=packages_to_remove))
    else:
        added_modules = current_modules

    added_module_packages = modules_packages(added_modules)
    current_packages = version.content.filter(pulp_type="rpm.package")
    packages_to_add = added_module_packages.difference(current_packages)
    version.add_content(Package.objects.filter(pk__in=packages_to_add))


def _create_snippet(snippet_string):
    """
    Create snippet of modulemd[-defaults] as artifact.

    Args:
        snippet_string (string):
            Snippet with modulemd[-defaults] yaml

    Returns:
        Snippet as unsaved Artifact object

    """
    tmp_file = tempfile.NamedTemporaryFile(dir=os.getcwd(), delete=False)
    with open(tmp_file.name, 'w') as snippet:
        snippet.write(snippet_string)
    return Artifact.init_and_validate(tmp_file.name)


def parse_modulemd(module_names, module_index):
    """
    Get modulemd NSVCA, artifacts, dependencies.

    Args:
        module_names (list):
            list of modulemd names
        module_index (mmdlib.ModuleIndex):
            libmodulemd index object

    """
    ret = list()
    for module in module_names:
        for s in module_index.get_module(module).get_all_streams():
            modulemd = dict()
            modulemd[PULP_MODULE_ATTR.NAME] = s.props.module_name
            modulemd[PULP_MODULE_ATTR.STREAM] = s.props.stream_name
            modulemd[PULP_MODULE_ATTR.VERSION] = s.props.version
            modulemd[PULP_MODULE_ATTR.CONTEXT] = s.props.context
            modulemd[PULP_MODULE_ATTR.ARCH] = s.props.arch
            modulemd[PULP_MODULE_ATTR.ARTIFACTS] = json.dumps(s.get_rpm_artifacts())

            dependencies_list = s.get_dependencies()
            dependencies = dict()
            for dep in dependencies_list:
                d_list = dep.get_runtime_modules()
                for dependency in d_list:
                    dependencies[dependency] = dep.get_runtime_streams(dependency)
            modulemd[PULP_MODULE_ATTR.DEPENDENCIES] = json.dumps(dependencies)
            # create yaml snippet for this modulemd stream
            temp_index = mmdlib.ModuleIndex.new()
            temp_index.add_module_stream(s)
            artifact = _create_snippet(temp_index.dump_to_string())
            modulemd["artifact"] = artifact
            ret.append(modulemd)
    return ret


def parse_defaults(module_index):
    """
    Get modulemd_defaults.

    Args:
        module_index (mmdlib.ModuleIndex):
            libmodulemd index object

    Returns:
        list of modulemd_defaults as dict

    """
    ret = list()
    modulemd_defaults = module_index.get_default_streams().keys()
    for module in modulemd_defaults:
        modulemd = module_index.get_module(module)
        defaults = modulemd.get_defaults()
        if defaults:
            default_stream = defaults.get_default_stream()
            default_profile = defaults.get_default_profiles_for_stream(default_stream)
            # create modulemd-default snippet
            temp_index = mmdlib.ModuleIndex.new()
            temp_index.add_defaults(defaults)
            artifact = _create_snippet(temp_index.dump_to_string())
            ret.append({
                PULP_MODULEDEFAULTS_ATTR.MODULE: modulemd.get_module_name(),
                PULP_MODULEDEFAULTS_ATTR.STREAM: default_stream,
                PULP_MODULEDEFAULTS_ATTR.PROFILES: json.dumps(default_profile),
                PULP_MODULEDEFAULTS_ATTR.DIGEST: artifact.sha256,
                'artifact': artifact
            })
    return ret
