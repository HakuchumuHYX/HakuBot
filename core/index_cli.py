"""Run the PJSK index maintenance service without importing a NoneBot entrypoint."""

import importlib.util
from utils.paths import project_root


def main(argv=None):
    path = project_root() / "plugins/pjsk_event_summary/services/index.py"
    spec = importlib.util.spec_from_file_location("hakubot_index_maintenance", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Index service is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
