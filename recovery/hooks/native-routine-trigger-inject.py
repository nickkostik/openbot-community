"""Canonical, non-activating injector for the reviewed native routine writer."""
import importlib.util
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    'openbot_native_routine_trigger_preservation',
    HERE / 'native-routine-trigger-preservation.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
patch = module.patch_component
