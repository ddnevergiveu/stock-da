# MarketLab：市场数据 SQL 分析与策略研究

将原来的 CSV → SQLite 学习脚本完善为两个可以独立运行的小项目：**数据质量与 SQL 分析**、**均线策略研究**。支持离线模拟数据、可重复导入、交易流水与回测结果导出。作者：姜圣涛 / [ddnevergiveu](https://github.com/ddnevergiveu)。

## 快速开始

需要 Python 3.10 或更高版本。

```bash
git clone https://github.com/ddnevergiveu/stock-da.git
cd stock-da
python -m venv .venv
```

激活环境：macOS / Linux 使用 `source .venv/bin/activate`；Windows PowerShell 使用 `.venv\Scripts\Activate.ps1`。

```bash
python -m pip install -r requirements.txt
python -m marketlab demo
```

演示固定随机种子为 42，产生 **600 根明确标为 synthetic 的模拟 K 线**。不下载数据，不需要 API key，不需要图形界面。`artifacts/demo/` 包括：

| 文件 | 用途 |
|---|---|
| `synthetic_bars.csv` | 模拟 OHLC / Tick 数据 |
| `market_data.db` | 规范化、带时间主键的 SQLite 数据库 |
| `equity.csv` | 每根信号、开盘仓位、净值、收益、回撤和基准净值 |
| `trades.csv` | 买卖时间、执行价格、份额、成本和每笔完整交易收益 |
| `metrics.json` | 汇总指标、数据质量、参数、成本与研究假设 |

同样参数重复运行结果一致、数据不重复导入。若改变 seed，请改用新的 `--output` 目录；不会用不同模拟价格悄悄覆盖旧数据库。

如果需要图片或安装命令行快捷入口：

```bash
python -m pip install -e ".[charts]"
marketlab demo --output artifacts/demo-chart --plot
```

图片保存在 `equity.png`。字体和绘图后端可用于无桌面的 Linux / CI，不依赖个人电脑的字体路径。

## 项目一：市场数据质量与 SQLite

```bash
python -m marketlab inspect --csv 15m_data.csv
python -m marketlab import --csv 15m_data.csv --db artifacts/market_data.db
# 再运行一次：0 条新增，已有且相同的数据自动跳过。
python -m marketlab import --csv 15m_data.csv --db artifacts/market_data.db
```

也保留原入口 `python import_to_sqlite.py --csv 15m_data.csv --db artifacts/market_data.db`。单纯 `import import_to_sqlite` 不再读取数据或改动数据库。

### 校验和导入规则

- 支持逗号、制表符、分号分隔的 UTF-8 文件。原 `15m_data.csv` 实际以制表符分隔。
- 必须有 `DateTime, Open, High, Low, Close, Volume, TickVolume`。额外列不导入；**每个数据库只表示一个品种**，不支持把多个品种的同一时间混在一起。
- 时间接受 `YYYY.MM.DD HH:MM:SS` 或 `YYYY-MM-DD HH:MM:SS`，规范为后者并按升序排列；不猜测时区，不接受带时区/不足秒精度的输入。
- 价格必须是有限正数，满足 `Low <= Open/Close <= High`。两种数量字段必须是非负整数。
- 完全相同的重复记录只留一份；同一时间、不同数值的记录直接报错。
- `DateTime` 主键同时提供唯一约束和索引。新增记录与重叠检查在一个事务中进行；已有记录内容冲突时，整个批次回滚。
- 默认写入 `artifacts/`，原仓库的 `market_data.db` 和 `15m_data.csv` 保留。旧数据库缺少主键，普通导入会拒绝。
- 确实需要重建自己指定的数据库时，显式加 `--replace`：只替换其中的 `market_data` 表，保留其他表。请给出你准备重建的目标路径。

```bash
python -m marketlab import --csv your_data.csv --db artifacts/your_data.db --replace
```

### SQL 学习与验证

`quires.sql` 已更名为 `queries.sql`，提供百分比涨幅、小时 Tick 统计、涨跌统计、每日最高价、星期分布、相对振幅、每日首末记录、价格区间、前高突破示例。安装 SQLite 命令行后运行：

```bash
sqlite3 -header -column artifacts/market_data.db < queries.sql
```

也可用任何 SQLite 可视化客户端打开生成的数据库。原日期带 `.` 时，`STRFTIME` 无法按预期识别；规范后的 ISO 时间能用于这些日期函数。参考 [SQLite 日期函数](https://www.sqlite.org/lang_datefunc.html)。

价格区间例子中的 22,800 等数值只是原练习的阈值，应按数据修改；示例 SQL 不代表投资信号。

## 项目二：均线策略研究

```bash
python -m marketlab backtest --csv 15m_data.csv --fast 20 --slow 60 --cost-bps 5 --output artifacts/research
```

执行过程可以从 `equity.csv` 和 `trades.csv` 逐条核对：

1. 第 t 根结束后，用截至该根的收盘价计算短/长均线。长均线尚未形成时持有现金。
2. 短均线高于长均线则目标为持有，否则为现金。
3. 第 t 根信号只在**下一条可观测 K 线的开盘价**执行，不能按产生信号的同根收盘价成交。
4. 全仓买入/全部卖出，允许小数份额；入场与出场均收费。`--cost-bps 5` 表示**每边 0.05%**的手续费与滑点合计。
5. 最后收盘强制平仓，并计入最后一次卖出成本。买入持有基准也包含买入与最终卖出的成本。

核心指标是总收益、最大回撤、完整交易数、扣成本胜率、累计成本和按 K 线数量计算的持仓比例。最大回撤用**每根收盘净值与初始资金**计算，不覆盖盘中可能发生的更深回撤。基准从第一根开盘起投资，因此有均线预热期的策略与基准暴露不同，比较时应结合持仓比例。

**默认不年化**。原数据有休市和不规则缺口，不能把所有 15 分钟数据直接当成每日数据或自动套用 252。只有你确认交易日历与数据频率后，才设置 `--periods-per-year`；随后输出基于每根收益的年化波动率与零无风险利率 Sharpe。该参数是一项用户指定假设，不会由程序推测。

### 数据事实与研究边界

仓库原数据共有 **201,676 条**，覆盖 **2016-11-15 06:30:00 至 2025-07-15 20:30:00**，原文件按时间倒序。`Volume` 全部为 0；`TickVolume` 是 Tick 计数，不能当成真实成交股数或成交金额。数据未提供可验证的品种、交易所、时区、复权或授权来源信息，不能根据价格水平擅自给它命名。

其中最长相邻记录缺口从 **2020-04-03 23:45:00 到 2020-04-15 21:45:00，约 11 天 22 小时**。程序报告最大缺口及两端时间，不补造缺失 K 线。回测在缺口中保持原有仓位，直到下一条可观测开盘才能执行；期间本应发生的均线变化和交易无法恢复，会影响收益和风险估计。

本项目展示研究工程与可核对的计算流程。模拟数据结果不代表真实业绩；原文件的结果也尚不足以支持实际交易。模型没有处理公司行动、融资、税费、实际成交量限制、动态点差、市场冲击或交易时段规则；没有做参数寻优或证明策略有效。

实现参考：[pandas 滚动窗口](https://pandas.pydata.org/docs/reference/api/pandas.Series.rolling.html)、[pandas shift](https://pandas.pydata.org/docs/reference/api/pandas.Series.shift.html)。

## 目录

| 路径 | 内容 |
|---|---|
| `marketlab/data.py` | 输入规范、校验、质量报告 |
| `marketlab/storage.py` | SQLite 事务与增量导入 |
| `marketlab/strategy.py` | 下一根开盘成交、资金/份额记账、回撤与成本 |
| `marketlab/demo.py` | 固定种子的模拟数据 |
| `marketlab/cli.py` | CLI 与结果导出 |
| `import_to_sqlite.py` | 原入口的兼容包装 |
| `queries.sql` | 可直接执行的 SQL 练习 |
| `tests/` | 数据、事务、时序与成本回归测试 |
| `.github/workflows/ci.yml` | Python 3.10 / 3.12 自动检查 |
| `15m_data.csv`, `market_data.db` | 保留的原始学习材料 |

## 验证

```bash
python -m unittest discover -s tests -v
python -m marketlab demo
python -m marketlab inspect --csv 15m_data.csv
```

测试覆盖无效 OHLC、日期、数量、重复冲突、幂等和增量导入、冲突整批回滚、显式替换、SQL 日期函数、真实下一根开盘执行、双边交易成本、未来数据不能改写过去、无交易场景、演示重现和所有 SQL 示例。

## 相关作品

- [电商分析与客户洞察](https://github.com/ddnevergiveu/data-analysis)
- [客户价值与复购预测](https://github.com/ddnevergiveu/machine-learning-project1)
