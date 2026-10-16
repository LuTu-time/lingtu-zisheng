-- ============================================================
-- 灵途自生 · 世界推演引擎 — SQLite 物理模型
-- 版本 1.0　生成于 2026-10-15 23:21:53
-- 由 tools/build_schema.py 从 data/schema.json 自动生成，请勿手改。
-- ============================================================

-- 建表期间关闭外键检查：部分表存在相互引用（character↔event），
-- SQLite 允许前向引用，但关闭检查可避免建表顺序带来的意外。
PRAGMA foreign_keys = OFF;
PRAGMA journal_mode = WAL;

-- [M099] 世界：全局单例。同一 seed 必须能复现同一个初始世界，这是可复现推演的前提。
CREATE TABLE IF NOT EXISTS "world" (
  "id" INTEGER PRIMARY KEY CHECK (id = 1),  -- 单例锁
  "seed" INTEGER NOT NULL,  -- 随机种子（M099），决定初始世界生成
  "name" TEXT NOT NULL,  -- 世界名，影响叙事风味
  "theme" TEXT NOT NULL DEFAULT 'lishi',  -- 题材标签 key，对应 genres.json，决定模块装载
  "tick" INTEGER NOT NULL DEFAULT 0,  -- 当前世界时刻（小时）。1 日 = 24 tick
  "tick_mode" TEXT NOT NULL DEFAULT 'normal',  -- 推演倍率：normal / fast / paused
  "calendar" TEXT NOT NULL DEFAULT '{}',  -- JSON 历法配置：每日小时、每旬日数、每季旬数
  "event_count" INTEGER NOT NULL DEFAULT 0,  -- 历史锚点①：已发生的事件条数 —— 哈希链防「改中间」，这个计数防「砍尾巴」
  "chain_head" TEXT NOT NULL DEFAULT '',  -- 历史锚点②：链尾指纹。与 event_count 联合承诺「历史一共走了这么远、最后停在这里」
  "created_at" TEXT NOT NULL DEFAULT (datetime('now'))  -- 真实创建时间，仅审计用
);

-- [M012] 势力：P0 只做两个势力（村民 / 野兽）。对外态度存 relation 表，不在这里存矩阵，避免双向不一致。
CREATE TABLE IF NOT EXISTS "faction" (
  "id" INTEGER PRIMARY KEY,
  "name" TEXT NOT NULL,  -- 势力名
  "kind" TEXT NOT NULL,  -- 村民 / 野兽 / 土匪
  "goal" TEXT NOT NULL DEFAULT '',  -- 势力目标，决定长期行为倾向
  "manpower" INTEGER NOT NULL DEFAULT 0,  -- 可动员人力
  "territory" INTEGER NOT NULL DEFAULT 0,  -- 控制地域数
  "cohesion" REAL NOT NULL DEFAULT 0.6,  -- 凝聚力 0~1（M044 组织演化）
  "martial" REAL NOT NULL DEFAULT 0.3,  -- 武力储备 0~1，power_scale 判定输入之一，非数值面板
  "status" TEXT NOT NULL DEFAULT 'active',  -- active / dissolved。G5 要求至少保留 2 个 active
  "color" TEXT NOT NULL DEFAULT '#534AB7',  -- 前端配色，便于可视化区分
  "updated_tick" INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS "idx_faction_status" ON "faction" ("status");

-- [M003] 关系：统一的有向边表，同时承载角色恩怨与势力态度。分开两张表会导致 M013 结盟博弈时多头查询。
CREATE TABLE IF NOT EXISTS "relation" (
  "id" INTEGER PRIMARY KEY,
  "from_kind" TEXT NOT NULL CHECK (from_kind IN ('character','faction')),  -- 起点实体类型
  "from_id" INTEGER NOT NULL,  -- 起点 id
  "to_kind" TEXT NOT NULL CHECK (to_kind IN ('character','faction')),  -- 终点实体类型
  "to_id" INTEGER NOT NULL,  -- 终点 id
  "kind" TEXT NOT NULL DEFAULT 'attitude',  -- 角色间：恩/怨/爱/忌；势力间：attitude
  "intensity" REAL NOT NULL DEFAULT 0,  -- -1 敌对 ~ +1 亲密。有向：允许 A 亲 B 而 B 仇 A
  "publicity" REAL NOT NULL DEFAULT 1.0,  -- 公开度 0~1。低值代表台面下的暗线关系
  "note" TEXT NOT NULL DEFAULT '',  -- 成因备注，指向触发事件
  "updated_tick" INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS "idx_relation_from" ON "relation" ("from_kind", "from_id");
CREATE INDEX IF NOT EXISTS "idx_relation_to" ON "relation" ("to_kind", "to_id");
CREATE UNIQUE INDEX IF NOT EXISTS "uq_relation" ON "relation" ("from_kind", "from_id", "to_kind", "to_id", "kind");

-- [M096] 事件：一切状态变更的载体，因果链的节点。cause_event_id 为空即链条起点（通常来自导演或作者干预）。
CREATE TABLE IF NOT EXISTS "event" (
  "id" INTEGER PRIMARY KEY,
  "tick" INTEGER NOT NULL,  -- 发生时刻
  "kind" TEXT NOT NULL,  -- harvest/disaster/feud/migration/death/alliance/... 决定图标与叙事模板
  "place_kind" TEXT NOT NULL DEFAULT 'none',  -- region / settlement / faction / none
  "place_id" INTEGER,
  "actor_kind" TEXT NOT NULL DEFAULT 'none',  -- 发起者类型：character / faction / nature / engine
  "actor_id" INTEGER,
  "participants" TEXT NOT NULL DEFAULT '[]',  -- JSON 参与者列表
  "cause_event_id" INTEGER REFERENCES event(id),  -- 因果父节点。M096 溯源链的边
  "depth" INTEGER NOT NULL DEFAULT 0,  -- 因果链深度。G2 规定 ≤ 4，超出降级为日志
  "effects" TEXT NOT NULL DEFAULT '{}',  -- JSON 状态变更明细（字段名 → 前值/后值）
  "visibility" TEXT NOT NULL DEFAULT 'public',  -- public / private / secret，决定谁可以知道它
  "summary" TEXT NOT NULL,  -- 人类可读一句话描述，直接用于纪事
  "prev_hash" TEXT NOT NULL DEFAULT '',  -- 前一条事件的指纹。与 hash 共同构成不可篡改的历史链
  "hash" TEXT NOT NULL DEFAULT ''  -- 本事件指纹 = sha256(prev_hash + 全字段载荷)。事后改一个字符，整条链从此断裂
);
CREATE INDEX IF NOT EXISTS "idx_event_tick" ON "event" ("tick");
CREATE INDEX IF NOT EXISTS "idx_event_cause" ON "event" ("cause_event_id");
CREATE INDEX IF NOT EXISTS "idx_event_kind" ON "event" ("kind");

-- 历史不可改写（事件）
-- 历史是已然发生的事实，不可改写。要改变过去，只能在某个历史点上另开一条时间线（见 snapshot 表），而不是回头篡改已发生的事。
-- 这两条触发器是硬约束：任何 UPDATE / DELETE 都会被数据库直接拒绝，
-- 无论它来自应用层、脚本，还是有人拿 sqlite3 手敲命令。
CREATE TRIGGER IF NOT EXISTS "trg_event_no_update"
BEFORE UPDATE ON "event"
BEGIN
  SELECT RAISE(ABORT, '历史不可修改：事件是已发生的事实。要改变过去，请在历史点上另开一条时间线。');
END;
CREATE TRIGGER IF NOT EXISTS "trg_event_no_delete"
BEFORE DELETE ON "event"
BEGIN
  SELECT RAISE(ABORT, '历史不可抹除：事件是已发生的事实。要改变过去，请在历史点上另开一条时间线。');
END;

-- [M103] 资源存量：粮食/银钱/药材/兵器等。供需总账 M103 的输入，也是砍树→粮食→人口这条主链的中间变量。
CREATE TABLE IF NOT EXISTS "stock" (
  "id" INTEGER PRIMARY KEY,
  "owner_kind" TEXT NOT NULL CHECK (owner_kind IN ('settlement','faction')),  -- 归属类型
  "owner_id" INTEGER NOT NULL,  -- 归属 id
  "resource_type" TEXT NOT NULL,  -- grain / wood / meat / medicine / coin / weapon
  "amount" REAL NOT NULL DEFAULT 0,  -- 存量。G3 保护可再生资源不得为负
  "quality" REAL NOT NULL DEFAULT 1.0,  -- 品质 0~1，影响实际可用量（M051 损耗）
  "decay_rate" REAL NOT NULL DEFAULT 0.0,  -- 每 tick 自然损耗率（粮食霉变、布匹虫蛀）
  "capacity" REAL NOT NULL DEFAULT 1000000,  -- 仓储容量上限，超出部分溢出损耗
  "updated_tick" INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS "uq_stock" ON "stock" ("owner_kind", "owner_id", "resource_type");

-- [M026] 粮食借据：聚落向村民势力借粮的运行台账；余额变化由不可变事件记录并受事件因果链追溯。
CREATE TABLE IF NOT EXISTS "grain_loan" (
  "id" INTEGER PRIMARY KEY,  -- 借据编号
  "principal" REAL NOT NULL CHECK (principal > 0),  -- 借入本金
  "balance" REAL NOT NULL CHECK (balance >= 0),  -- 未偿余额
  "issued_tick" INTEGER NOT NULL,  -- 放款时刻
  "due_tick" INTEGER NOT NULL,  -- 到期时刻
  "status" TEXT NOT NULL CHECK (status IN ('active','overdue','repaid')),  -- 借据状态
  "last_settled_tick" INTEGER NOT NULL,  -- 最后结息时刻
  "issue_event_id" INTEGER NOT NULL REFERENCES event(id),  -- 放款事件
  "settlement_id" INTEGER NOT NULL REFERENCES settlement(id),  -- 借款聚落
  "lender_faction_id" INTEGER NOT NULL REFERENCES faction(id)  -- 放款势力
);
CREATE INDEX IF NOT EXISTS "idx_grain_loan_due" ON "grain_loan" ("status", "due_tick");

-- [M100] 设定事实：作品世界观的既有事实。source='canon' 的强制只读，是 G7 一致性硬约束的判据。
CREATE TABLE IF NOT EXISTS "canon_fact" (
  "id" INTEGER PRIMARY KEY,
  "content" TEXT NOT NULL,  -- 事实内容
  "source" TEXT NOT NULL DEFAULT 'engine' CHECK (source IN ('canon','author','engine')),  -- canon 原作 / author 作者 / engine 引擎推断
  "mutable" INTEGER NOT NULL DEFAULT 1,  -- 0 = 只读（G7 拦截冲突生成），1 = 引擎可改写
  "created_tick" INTEGER NOT NULL DEFAULT 0,
  "note" TEXT NOT NULL DEFAULT ''  -- 来源出处说明
);
CREATE INDEX IF NOT EXISTS "idx_canon_mutable" ON "canon_fact" ("mutable");
CREATE TRIGGER IF NOT EXISTS "trg_canon_fact_protect_update"
BEFORE UPDATE ON "canon_fact" WHEN OLD.mutable = 0 OR OLD.source = 'canon'
BEGIN
  SELECT RAISE(ABORT, '已确认设定不可改写；请追加新设定说明。');
END;
CREATE TRIGGER IF NOT EXISTS "trg_canon_fact_protect_delete"
BEFORE DELETE ON "canon_fact" WHEN OLD.mutable = 0 OR OLD.source = 'canon'
BEGIN
  SELECT RAISE(ABORT, '已确认设定不可改写；请追加新设定说明。');
END;

-- [M094] 模块实例：调度器 M094 的产物。世界开局的题材标签经三档模型解析后，落成这里的每一行。
CREATE TABLE IF NOT EXISTS "module_instance" (
  "id" INTEGER PRIMARY KEY,
  "module_id" TEXT NOT NULL UNIQUE,  -- 对应 modules.json 的 M### 编号
  "scope" TEXT NOT NULL CHECK (scope IN ('global','routed')),  -- 作用域
  "state" TEXT NOT NULL CHECK (state IN ('on','simple','off')),  -- 三档状态。simple = 实体存在但不跑完整推演循环
  "params" TEXT NOT NULL DEFAULT '{}',  -- JSON 模块参数（阈值、速率等覆盖项）
  "reason" TEXT NOT NULL DEFAULT ''  -- 状态来源：默认 / 题材挂载 / 作者强制
);
CREATE INDEX IF NOT EXISTS "idx_module_state" ON "module_instance" ("state");

-- [M107] 快照：支持回滚与时间线分支。parent_id 构成一棵树，分叉即为平行世界。
CREATE TABLE IF NOT EXISTS "snapshot" (
  "id" INTEGER PRIMARY KEY,
  "tick" INTEGER NOT NULL,  -- 快照时刻
  "state_digest" TEXT NOT NULL,  -- 状态摘要哈希，用于快速比对两个分支是否已分叉
  "label" TEXT NOT NULL DEFAULT '',  -- 人类可读标签
  "parent_id" INTEGER REFERENCES snapshot(id),  -- 分支父节点，空则为根
  "summary" TEXT NOT NULL DEFAULT '',  -- 一行摘要：此刻世界处于什么状态
  "created_at" TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS "idx_snapshot_tick" ON "snapshot" ("tick");

-- [M099] 地域：地理与自然资源的最小单位，是生态类模块（M048/M050/M069）的作用面。
CREATE TABLE IF NOT EXISTS "region" (
  "id" INTEGER PRIMARY KEY,
  "world_id" INTEGER NOT NULL REFERENCES world(id),
  "name" TEXT NOT NULL,  -- 地名
  "terrain" TEXT NOT NULL,  -- 平原 / 丘陵 / 林地 / 河谷
  "climate" TEXT NOT NULL,  -- 温带 / 干旱 / 寒冷
  "forest" REAL NOT NULL DEFAULT 0.6,  -- 林木覆盖率 0~1（G3 可再生资源，不得为负）
  "game_density" REAL NOT NULL DEFAULT 0.5,  -- 猎物密度 0~1，随植被变化（M069）
  "water_health" REAL NOT NULL DEFAULT 0.8,  -- 水源健康度 0~1（M052）
  "fertility" REAL NOT NULL DEFAULT 0.7,  -- 土壤肥力 0~1，影响收成，长期耕作会衰减（M047）
  "owner_faction" INTEGER REFERENCES faction(id),  -- 归属势力，可空（无主之地）
  "updated_tick" INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS "idx_region_world" ON "region" ("world_id");

-- [M045] 聚落：村落 / 集镇 / 城池。社会推演的主要载体，人口与民心的变化都落在这里。
CREATE TABLE IF NOT EXISTS "settlement" (
  "id" INTEGER PRIMARY KEY,
  "region_id" INTEGER NOT NULL REFERENCES region(id),
  "name" TEXT NOT NULL,  -- 聚落名
  "tier" TEXT NOT NULL DEFAULT '村',  -- 村 / 镇 / 城（M045 规模等级）
  "population" INTEGER NOT NULL DEFAULT 0,  -- 人口。受 G1 人口下限锁保护
  "households" INTEGER NOT NULL DEFAULT 0,  -- 户数（M046 户籍）
  "order_value" REAL NOT NULL DEFAULT 0.6,  -- 秩序值 0~1。过低易生治安事件（M017/M018）
  "morale" REAL NOT NULL DEFAULT 0.6,  -- 民心 0~1。粮食短缺直接压低它，是人口流失的前置变量
  "housing" INTEGER NOT NULL DEFAULT 0,  -- 住房容量，限制人口上限
  "faction_id" INTEGER REFERENCES faction(id),  -- 主导势力
  "founded_tick" INTEGER NOT NULL DEFAULT 0,  -- 建立时刻
  "updated_tick" INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS "idx_settlement_region" ON "settlement" ("region_id");

-- [M001] 角色：由 M001 档案 / M002 动机 / M104 生命周期共同维护。profile 与 traits 是 LLM 生成台词时的人格锚点。
CREATE TABLE IF NOT EXISTS "character" (
  "id" INTEGER PRIMARY KEY,
  "settlement_id" INTEGER REFERENCES settlement(id),  -- 所属聚落，可空（流民/独行）
  "faction_id" INTEGER REFERENCES faction(id),  -- 所属势力
  "name" TEXT NOT NULL,  -- 姓名
  "occupation" TEXT NOT NULL DEFAULT '农人',  -- 农夫 / 猎人 / 村长 / 铁匠（决定作息与产出）
  "age" INTEGER NOT NULL DEFAULT 20,  -- 年龄，随年推进（M104）
  "health" REAL NOT NULL DEFAULT 1.0,  -- 健康 0~1
  "status" TEXT NOT NULL DEFAULT 'alive',  -- alive / dead / left（迁出）。G1 只统计 alive
  "profile" TEXT NOT NULL DEFAULT '{}',  -- JSON：出身/外貌/性格/习惯/执念/恐惧/秘密
  "traits" TEXT NOT NULL DEFAULT '{}',  -- JSON：性格倾向权重，驱动 M002 决策
  "needs" TEXT NOT NULL DEFAULT '{}',  -- JSON：食物/木材/安全等需求的当前满足度
  "goal_stack" TEXT NOT NULL DEFAULT '[]',  -- JSON：目标栈（M002），栈顶为当前目标
  "death_event_id" INTEGER REFERENCES event(id),  -- 死亡因果事件。G6 要求非空即可回溯
  "born_tick" INTEGER NOT NULL DEFAULT 0,
  "died_tick" INTEGER,
  "updated_tick" INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS "idx_character_settlement" ON "character" ("settlement_id");
CREATE INDEX IF NOT EXISTS "idx_character_status" ON "character" ("status");

-- [M001] 记忆：角色私有。记忆可失真 —— 失真本身是剧情素材，因此 content 不保证等于事实。
CREATE TABLE IF NOT EXISTS "memory" (
  "id" INTEGER PRIMARY KEY,
  "owner_id" INTEGER NOT NULL REFERENCES character(id),  -- 记忆所有者
  "content" TEXT NOT NULL,  -- 记忆内容（第一人称）
  "importance" REAL NOT NULL DEFAULT 0.5,  -- 重要度 0~1，检索排序用
  "tick" INTEGER NOT NULL,  -- 事件发生时刻
  "event_id" INTEGER REFERENCES event(id),  -- 来源事件，可空（主观印象类记忆）
  "decay" REAL NOT NULL DEFAULT 1.0  -- 衰减系数，随 tick 递减；低到阈值以下可被遗忘
);
CREATE INDEX IF NOT EXISTS "idx_memory_owner" ON "memory" ("owner_id", "tick");

PRAGMA foreign_keys = ON;
