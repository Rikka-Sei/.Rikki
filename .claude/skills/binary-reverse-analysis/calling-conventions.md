# 调用约定识别参考

被 `SKILL.md` §常见错误表引用。逆向推断函数参数传递时查阅。

## x86 (MSVC, 32 位 Windows)

| 约定 | this 指针 | 参数传递 | 清栈方 |
|------|----------|---------|--------|
| cdecl | — | 栈，右到左 | 调用方 |
| stdcall | — | 栈，右到左 | 被调方 |
| fastcall | — | ECX/EDX 前 2 参，余栈 | 被调方 |
| **thiscall** | **ECX** | **余参压栈，右到左** | **被调方** |

**thiscall 关键**：`this`→ECX，后续参数走**栈**（不是寄存器）。这是逆向 C++ 成员函数最常误判的点。

## x64 (Windows)

单一约定，无 cdecl/stdcall/thiscall 之分：
- 整数/指针参数：RCX, RDX, R8, R9（前 4 个），余参压栈
- `this` 指针：RCX
- 布尔参数：走寄存器（如 DL，即 RDX 低 8 位）
- 清栈：调用方

## x64 (Linux/SysV)

- 整数/指针参数：RDI, RSI, RDX, RCX, R8, R9（前 6 个），余参压栈
- `this` 指针：RDI
- 清栈：调用方

## ARM

- AAPCS 约定：R0-R3 前 4 参，余参压栈
- `this` 指针：R0
- 详见 ARM AAPCS 文档

## 工具误判警告

**Ghidra 会把 thiscall 误判为 cdecl**——工具不可全信。
必须人工识别，不要依赖工具的调用约定标注。

### 人工识别方法

1. 看 `this` 指针来源：ECX (x86 thiscall) / RCX (x64 Win) / RDI (x64 Linux) / R0 (ARM)
2. 看参数传递方式：栈（x86 thiscall 余参）还是寄存器（x64）
3. 看清栈方：被调方（x86 thiscall/stdcall）还是调用方（cdecl/x64）
4. 看函数序言/尾声的栈调整指令

## 跨架构不可移植惯用语

**禁止把一架构的字节惯用语直接套到另一架构**。

| 场景 | x64 (Windows) | x86 (thiscall) |
|------|--------------|----------------|
| 布尔参数传递 | `dl` 寄存器（`B2 XX` / `0F B6 D0`） | 栈（`6a XX` push / `ff b5` push [ebp-x]） |
| this 指针 | RCX | ECX |
| 余参 | RDX/R8/R9 | 压栈 |

**反例**：用 x64 的 `B2 01 EB 02 32 D2`（dl 惯用语）搜 i386 thiscall 调用点，可能造成大量假阳性。i386 thiscall 布尔走栈，签名应是 `6a 01 ... ff b5 ... e8`。

## 外部权威

- [x86 calling conventions - Wikipedia](https://en.wikipedia.org/wiki/X86_calling_conventions)
- [Agner Fog - Calling conventions](https://www.agner.org/optimize/calling_conventions.pdf)
- [Microsoft x64 ABI](https://learn.microsoft.com/en-us/cpp/build/x64-calling-convention)
