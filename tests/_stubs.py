import sys
import types

if "serverchan_sdk" not in sys.modules:
    _stub = types.ModuleType("serverchan_sdk")
    _stub.sc_send = lambda *args, **kwargs: {"code": 0}
    sys.modules["serverchan_sdk"] = _stub
