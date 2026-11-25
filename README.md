# 行进演出的承接台词与场次换线

这是面向“演出场记、场景台词与游客游线”业务文本的 Python 项目。当前目录提供合成练习语料和已登记的原始记录，记录用于开发与回归时复核文字内容和来源关系。

## 内容

- data/corpus.json 保存合成文本记录及记录类别。
- data/context.json 说明当前语料范围。
- materials.py 读取项目语料，保留每条记录的原始文字。
- routeline.py 从剧本文字与场记里恢复关系，排演前查出会错接的台词，并在换线后重排承接场次。
- tests/ 检查资料读取入口、语料索引，以及台词判定与换线安排的回归。

## 运行和检查

项目仅依赖 Python 标准库。运行测试：

    python3 -m unittest discover -s tests -v

## 分析结论怎么看

语料里只有记录原文，所有判定都要能回到原句或场记。routeline.py 按以下层次读：

- **台词与动作分开。** 引号内是角色说的话，引号外是动作叙述或对文字的说明。`hill-a` 的“交旗由本场观众完成”是动作，`water-a` 的“白旗来自水边引子”是来历说明。
- **语气分开。** “角色应允”只认第一人称当场承诺；同行邀请是“提议”；“别把白旗交给……”是“反话/告诫”；`bridge-d` 的“我许你还来的旧物”是冒认旧诺，按反话处理。
- **省掉的成分按场记补回。** `bridge-c` 的“你”只能是刚交过旗的本场观众，`bridge-d` 的“你”只能是手上仍有旗的观众。补不回来就不许对那组说。
- **身份按场次与令牌适用范围认。** 何岚在甲组是守寨人、在乙组是商队掌柜，合场时两重身份都在场，但关系不能互相借用；对甲组说商队的话会被判错接。
- **承诺跟着人走。** 甲组的“带过桥”是已说出口的承诺，换线、跳幕、合场都不清空；`bridge-d` 既没有对应的交旗记录，也没有对应的旧诺，对哪组都不能用。

## 命令

    python3 -I routeline.py report                       # 幕、台词、语气、依赖、场记摘要
    python3 -I routeline.py plan --change c1             # 应用场务公告 c1（山腰暂停、甲乙合场）
    python3 -I routeline.py plan --after-water           # 跳过 hill-a 后重排
    python3 -I routeline.py plan --skip hill-a --skip bridge-c
    python3 -I routeline.py check --group 乙组 --lineup bridge-c   # 排演前查错接
    python3 -I routeline.py show --group 甲组             # 回查该组实际听过哪些话
    python3 -I routeline.py validate                     # 语料自洽检查

退出码：`report`/`plan`/`show` 成功为 0；`check` 发现错接、`validate` 发现结构问题为 1；输入错误为 2。

`plan` 的输出顺序对应换线时的处置顺序：先用两组都听得懂的共用提示（`bridge-b`，提示此刻不提旗），再给单组的承接提示（甲组 `bridge-c`，且不透露乙组的身份与剧情）；若跳过 `bridge-c`，会在“未履行承诺”里列出甲组仍可先演的幕和必须分组补演的内容与原因。
