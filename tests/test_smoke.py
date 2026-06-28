from sfpy import __asm__


CALL_CLOBBERS = [
    "rax",
    "rcx",
    "rdx",
    "rsi",
    "rdi",
    "r8",
    "r9",
    "r10",
    "r11",
    "memory",
    "cc",
]


def test_integer_readwrite_operand():
    assert __asm__("addq %1, %0", [("+r", 40)], [("r", 2)], ["cc"]) == 42


def test_multiple_outputs():
    assert __asm__(
        "movq %2, %0\nmovq %3, %1",
        [("=&r", 0), ("=&r", 0)],
        [("r", 7), ("r", 9)],
        [],
    ) == (7, 9)


def test_python_callable_operand():
    def callback():
        return 123

    assert (
        __asm__(
            "call *%1\nmovq %%rax, %0",
            [("=r", 0)],
            [("r", callback)],
            CALL_CLOBBERS,
        )
        == 123
    )
