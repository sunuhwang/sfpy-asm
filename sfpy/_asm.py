from __future__ import annotations

import hashlib
import importlib.util
import importlib.machinery
import os
import shlex
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path
from typing import Any


_TYPE_INT = "i"
_TYPE_BOOL = "b"
_TYPE_DOUBLE = "d"
_TYPE_CSTR = "s"
_TYPE_BYTES = "y"
_TYPE_OBJECT = "o"
_TYPE_CALLABLE = "f"


def __asm__(
    code: str,
    outputs: list[tuple[str, Any]] | None = None,
    inputs: list[tuple[str, Any]] | None = None,
    clobbers: list[str] | None = None,
) -> Any:
    if not isinstance(code, str):
        raise TypeError("code must be str")

    out_ops = _normalize_operands(outputs, "outputs")
    in_ops = _normalize_operands(inputs, "inputs")
    clobber_list = _normalize_clobbers(clobbers)

    out_types = [_classify_output(value) for _, value in out_ops]
    in_types = [_classify_input(value) for _, value in in_ops]
    module = _load_module(code, out_ops, in_ops, clobber_list, out_types, in_types)

    out_values = [value for _, value in out_ops]
    in_values = [value for _, value in in_ops]
    return module.run(out_values, in_values)


def _normalize_operands(value: Any, name: str) -> list[tuple[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"{name} must be a list of (constraint, value) tuples")

    result: list[tuple[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, tuple) or len(item) != 2:
            raise TypeError(f"{name}[{index}] must be a (constraint, value) tuple")
        constraint, operand_value = item
        if not isinstance(constraint, str):
            raise TypeError(f"{name}[{index}][0] must be str")
        result.append((constraint, operand_value))
    return result


def _normalize_clobbers(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("clobbers must be a list of strings")
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise TypeError(f"clobbers[{index}] must be str")
    return value


def _classify_output(value: Any) -> str:
    if isinstance(value, bool):
        return _TYPE_BOOL
    if isinstance(value, int):
        return _TYPE_INT
    if isinstance(value, float):
        return _TYPE_DOUBLE
    if value is None:
        return _TYPE_INT
    raise TypeError("output values must be int, bool, float, or None")


def _classify_input(value: Any) -> str:
    if callable(value):
        return _TYPE_CALLABLE
    if isinstance(value, bool):
        return _TYPE_BOOL
    if isinstance(value, int):
        return _TYPE_INT
    if isinstance(value, float):
        return _TYPE_DOUBLE
    if isinstance(value, str):
        return _TYPE_CSTR
    if isinstance(value, (bytes, bytearray)):
        return _TYPE_BYTES
    return _TYPE_OBJECT


def _load_module(
    code: str,
    outputs: list[tuple[str, Any]],
    inputs: list[tuple[str, Any]],
    clobbers: list[str],
    out_types: list[str],
    in_types: list[str],
) -> Any:
    key_material = repr(
        (
            sys.version_info[:2],
            code,
            [constraint for constraint, _ in outputs],
            [constraint for constraint, _ in inputs],
            clobbers,
            out_types,
            in_types,
        )
    ).encode()
    key = hashlib.sha256(key_material).hexdigest()
    build_dir = Path(tempfile.gettempdir()) / "sfpy_asm" / key
    module_name = f"_sfpy_asm_{key}"
    ext_suffix = importlib.machinery.EXTENSION_SUFFIXES[0]
    so_path = build_dir / f"{module_name}{ext_suffix}"

    if not so_path.exists():
        build_dir.mkdir(parents=True, exist_ok=True)
        c_path = build_dir / f"{module_name}.c"
        c_path.write_text(
            _render_source(module_name, code, outputs, inputs, clobbers, out_types, in_types),
            encoding="utf-8",
        )
        _compile(c_path, so_path)

    spec = importlib.util.spec_from_file_location(module_name, so_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load generated asm module: {so_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _compile(c_path: Path, so_path: Path) -> None:
    include = sysconfig.get_paths()["include"]
    cflags = sysconfig.get_config_var("CFLAGS") or ""
    ldflags = sysconfig.get_config_var("LDFLAGS") or ""
    cmd = [
        "gcc",
        "-shared",
        "-fPIC",
        "-O2",
        *shlex.split(cflags),
        f"-I{include}",
        str(c_path),
        "-o",
        str(so_path),
        *shlex.split(ldflags),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("gcc is required at runtime for sfpy.__asm__") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "failed to compile generated inline assembly module\n"
            f"command: {' '.join(shlex.quote(part) for part in cmd)}\n"
            f"stdout:\n{exc.stdout}\n"
            f"stderr:\n{exc.stderr}"
        ) from exc


def _render_source(
    module_name: str,
    code: str,
    outputs: list[tuple[str, Any]],
    inputs: list[tuple[str, Any]],
    clobbers: list[str],
    out_types: list[str],
    in_types: list[str],
) -> str:
    return f"""#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdint.h>

static PyObject **sfpy_callable_inputs = NULL;

static int sfpy_to_ll(PyObject *obj, long long *out);

{_render_callback_functions(in_types)}

static int sfpy_to_ll(PyObject *obj, long long *out) {{
    int overflow = 0;
    long long value = PyLong_AsLongLongAndOverflow(obj, &overflow);
    if (value == -1 && PyErr_Occurred()) return -1;
    if (overflow) {{
        PyErr_SetString(PyExc_OverflowError, "integer operand does not fit in long long");
        return -1;
    }}
    *out = value;
    return 0;
}}

static int sfpy_convert_output(PyObject *obj, const char type, void *slot) {{
    if (type == '{_TYPE_DOUBLE}') {{
        double value = PyFloat_AsDouble(obj);
        if (value == -1.0 && PyErr_Occurred()) return -1;
        *(double *)slot = value;
        return 0;
    }}
    long long value = 0;
    if (obj != Py_None && sfpy_to_ll(obj, &value) < 0) return -1;
    *(long long *)slot = value;
    return 0;
}}

static PyObject *sfpy_make_output(const char type, void *slot) {{
    if (type == '{_TYPE_BOOL}') return PyBool_FromLong(*(long long *)slot != 0);
    if (type == '{_TYPE_DOUBLE}') return PyFloat_FromDouble(*(double *)slot);
    return PyLong_FromLongLong(*(long long *)slot);
}}

static PyObject *sfpy_run(PyObject *self, PyObject *args) {{
    PyObject *outputs_obj = NULL;
    PyObject *inputs_obj = NULL;
    if (!PyArg_ParseTuple(args, "OO", &outputs_obj, &inputs_obj)) return NULL;
    if (!PyList_Check(outputs_obj) || PyList_GET_SIZE(outputs_obj) != {len(outputs)}) {{
        PyErr_SetString(PyExc_TypeError, "internal error: invalid output list");
        return NULL;
    }}
    if (!PyList_Check(inputs_obj) || PyList_GET_SIZE(inputs_obj) != {len(inputs)}) {{
        PyErr_SetString(PyExc_TypeError, "internal error: invalid input list");
        return NULL;
    }}

{_render_native_declarations(out_types, in_types)}
{_render_native_conversions(out_types, in_types)}

    sfpy_callable_inputs = {("NULL" if _TYPE_CALLABLE not in in_types else "in_callable_slots")};
    __asm__ volatile (
        {_c_string(code)}
        {_render_asm_operands(outputs, inputs, clobbers)}
    );
    sfpy_callable_inputs = NULL;
{_render_buffer_releases(in_types)}

{_render_return(out_types)}
}}

static PyMethodDef sfpy_methods[] = {{
    {{"run", sfpy_run, METH_VARARGS, NULL}},
    {{NULL, NULL, 0, NULL}}
}};

static struct PyModuleDef sfpy_module = {{
    PyModuleDef_HEAD_INIT,
    "{module_name}",
    NULL,
    -1,
    sfpy_methods
}};

PyMODINIT_FUNC PyInit_{module_name}(void) {{
    return PyModule_Create(&sfpy_module);
}}
"""


def _render_callback_functions(in_types: list[str]) -> str:
    chunks: list[str] = []
    for index, typ in enumerate(in_types):
        if typ != _TYPE_CALLABLE:
            continue
        chunks.append(
            f"""static long long sfpy_callback_{index}(void) {{
    PyObject *result = PyObject_CallNoArgs(sfpy_callable_inputs[{index}]);
    if (result == NULL) return 0;
    long long value = 0;
    if (result != Py_None && sfpy_to_ll(result, &value) < 0) {{
        PyErr_Clear();
        value = 0;
    }}
    Py_DECREF(result);
    return value;
}}
"""
        )
    return "\n".join(chunks)


def _render_native_declarations(out_types: list[str], in_types: list[str]) -> str:
    lines: list[str] = []
    for index, typ in enumerate(out_types):
        ctype = "double" if typ == _TYPE_DOUBLE else "long long"
        lines.append(f"    {ctype} out_{index} = 0;")
    for index, typ in enumerate(in_types):
        if typ == _TYPE_DOUBLE:
            lines.append(f"    double in_{index} = 0;")
        elif typ == _TYPE_CSTR:
            lines.append(f"    const char *in_{index} = NULL;")
        elif typ == _TYPE_BYTES:
            lines.append(f"    void *in_{index} = NULL;")
            lines.append(f"    Py_buffer in_{index}_view = {{0}};")
        elif typ == _TYPE_CALLABLE:
            lines.append(f"    long long (*in_{index})(void) = sfpy_callback_{index};")
        elif typ == _TYPE_OBJECT:
            lines.append(f"    PyObject *in_{index} = NULL;")
        else:
            lines.append(f"    long long in_{index} = 0;")
    if _TYPE_CALLABLE in in_types:
        lines.append(f"    PyObject *in_callable_slots[{len(in_types)}] = {{0}};")
    return "\n".join(lines)


def _render_native_conversions(out_types: list[str], in_types: list[str]) -> str:
    lines: list[str] = []
    for index, typ in enumerate(out_types):
        lines.append(
            f"    if (sfpy_convert_output(PyList_GET_ITEM(outputs_obj, {index}), '{typ}', &out_{index}) < 0) return NULL;"
        )
    for index, typ in enumerate(in_types):
        item = f"PyList_GET_ITEM(inputs_obj, {index})"
        if typ == _TYPE_DOUBLE:
            lines.append(f"    in_{index} = PyFloat_AsDouble({item});")
            lines.append("    if (PyErr_Occurred()) return NULL;")
        elif typ == _TYPE_CSTR:
            lines.append(f"    in_{index} = PyUnicode_AsUTF8({item});")
            lines.append(f"    if (in_{index} == NULL) return NULL;")
        elif typ == _TYPE_BYTES:
            lines.append(f"    if (PyObject_GetBuffer({item}, &in_{index}_view, PyBUF_SIMPLE) < 0) return NULL;")
            lines.append(f"    in_{index} = in_{index}_view.buf;")
        elif typ == _TYPE_CALLABLE:
            lines.append(f"    in_callable_slots[{index}] = {item};")
            lines.append(f"    if (!PyCallable_Check(in_callable_slots[{index}])) {{ PyErr_SetString(PyExc_TypeError, \"callable operand expected\"); return NULL; }}")
        elif typ == _TYPE_OBJECT:
            lines.append(f"    in_{index} = {item};")
        else:
            lines.append(f"    if (sfpy_to_ll({item}, &in_{index}) < 0) return NULL;")
    return "\n".join(lines)


def _render_buffer_releases(in_types: list[str]) -> str:
    lines = []
    for index, typ in enumerate(in_types):
        if typ == _TYPE_BYTES:
            lines.append(f"    PyBuffer_Release(&in_{index}_view);")
    return "\n".join(lines)


def _render_asm_operands(
    outputs: list[tuple[str, Any]],
    inputs: list[tuple[str, Any]],
    clobbers: list[str],
) -> str:
    output_parts = [f"{_c_string(constraint)}(out_{index})" for index, (constraint, _) in enumerate(outputs)]
    input_parts = [f"{_c_string(constraint)}(in_{index})" for index, (constraint, _) in enumerate(inputs)]
    clobber_parts = [_c_string(clobber) for clobber in clobbers]
    return (
        "\n        : "
        + ", ".join(output_parts)
        + "\n        : "
        + ", ".join(input_parts)
        + "\n        : "
        + ", ".join(clobber_parts)
    )


def _render_return(out_types: list[str]) -> str:
    if not out_types:
        return "    Py_RETURN_NONE;"
    if len(out_types) == 1:
        return f"    return sfpy_make_output('{out_types[0]}', &out_0);"

    lines = [f"    PyObject *tuple = PyTuple_New({len(out_types)});", "    if (tuple == NULL) return NULL;"]
    for index, typ in enumerate(out_types):
        lines.append(f"    PyObject *item_{index} = sfpy_make_output('{typ}', &out_{index});")
        lines.append(f"    if (item_{index} == NULL) {{ Py_DECREF(tuple); return NULL; }}")
        lines.append(f"    PyTuple_SET_ITEM(tuple, {index}, item_{index});")
    lines.append("    return tuple;")
    return "\n".join(lines)


def _c_string(value: str) -> str:
    return '"' + value.encode("unicode_escape").decode("ascii").replace('"', r"\"") + '"'
