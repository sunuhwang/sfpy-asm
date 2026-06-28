# sfpy-asm(Segmentation Fault in Python)

Experimental Python bridge for GCC/GAS-style extended inline assembly.

```python
from sfpy import __asm__

result = __asm__(
    "addq %1, %0",
    [("+r", 40)],
    [("r", 2)],
    ["cc"],
)

assert result == 42
```

Only `__asm__` is exported from `sfpy`.

## API

```python
__asm__(code: str, outputs=None, inputs=None, clobbers=None)
```

- `code`: GCC extended inline assembly template. GAS syntax is accepted by GCC.
- `outputs`: list of `(constraint, value)` output operands.
- `inputs`: list of `(constraint, value)` input operands.
- `clobbers`: list of register/clobber names such as `["rax", "memory", "cc"]`.

Return value:

- no outputs: `None`
- one output: that value
- multiple outputs: tuple of values

Supported operand values:

- `int`, `bool`: native `long long`
- `float`: native `double`
- `str`: UTF-8 `const char *`
- `bytes`, `bytearray`: byte buffer pointer
- any other object: raw `PyObject *`
- Python callables used as inputs: native no-argument callback pointer returning `long long`

Python variables cannot be mutated through GCC output operands, so output operands are
returned instead. For read/write constraints such as `"+r"`, the supplied output value is
used as the initial native value.

Callable operands are plain native function pointers. Call them from assembly with
`call *%N`. Because GCC cannot see that call, include the usual caller-saved register
clobbers yourself:

```python
def answer():
    return 42

result = __asm__(
    "call *%1\nmovq %%rax, %0",
    [("=r", 0)],
    [("r", answer)],
    ["rax", "rcx", "rdx", "rsi", "rdi", "r8", "r9", "r10", "r11", "memory", "cc"],
)
```

This package is Linux/WSL-only and requires `gcc` plus Python development headers at
runtime because `__asm__` compiles a small shared object for each assembly signature.
