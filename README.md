# dc_agent（Data Copilot Agent）— 代码隐身、业务显形的业务逻辑资产与智能问答台

**`dc_agent`** 借鉴了 `dq_agent` 的严格单向分层架构与 ProdAgent 微内核（零技术债务 + 并发事务锁 + 哈希链事件账本 + 乐观锁 CAS），吸收了 `sql_explorer` 的 SQL AST 语义提取与强制披露（`disclose`）机制：

- **输入端（双源接入）**：一边吃进**监管/业务制度材料**，一边静默解析**SQL / 存储过程代码逻辑**；
- **中间层（业务资产层）**：代码本身（物理表名、字段名、SQL 语句）被严格封存为**底层隐身实证依据**，自动与监管条款融合，提炼出 4 类纯业务语言资产：
  1. **业务版图卡**：系统逻辑已经包含了哪些业务板块、覆盖度如何；
  2. **业务处理规则卡**：哪些情形纳入、哪些情况自动剔除、不同主体走哪些分支、如何防重；
  3. **业务指标口径卡**：业务数字怎么计算、静态快照与日均口径的易混提示；
  4. **制度 vs 逻辑对账卡**：已一致落地 / 存在多套分歧 / 仅制度提及尚未包含；
- **输出端（纯业务问答 Copilot）**：业务人员用日常语言提问（如“我们的逻辑里已经包含了哪些业务？”“为什么某些账户会被剔除？”），系统只用 **100% 业务人员听得懂的语言**回答，附带可点击核验的业务依据，并由 `Bus` 的**零代码黑话门禁**把关，绝不吐出一行代码或英文表名。

---

## 快速启动

### 1. 后端启动（复用同级 `dq_agent/.venv` 或新建虚拟环境）
```bash
cd backend
../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload 2>/dev/null || ../../dq_agent/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload
```

### 2. 前端启动
```bash
cd frontend
npm install
npm run dev
```

### 3. 运行后端全部测试（含架构红线 + 去技术化门禁 + 并发压测 + 真实 SAC 语料实测）
- 可通过环境变量 `SQL_EXPLORER_DIR` 指定真实 SAC 语料库目录（默认自动查找同级目录 `../sql_explorer`）；
- 接口 `POST /api/sources/load-sac-real` 支持一键载入真实 SAC 语料与监管规范库。

```bash
cd backend
../../dq_agent/.venv/bin/pytest -q -W error::sqlalchemy.exc.SAWarning
```
