# loverbot 架构设计

> 需求编号（R1–R7 / A1–A16 / P1 情绪原则）指向需求定义文档《AI 恋人需求定义》。

## 一、总体形态

单进程 asyncio 应用，四个长生命周期组件 + 一组纯逻辑子系统：

```
┌────────────────────────── loverbot 进程 ──────────────────────────┐
│                                                                    │
│  主 bot（PTB 轮询,ALL_TYPES）      导演 bot（PTB 轮询,只认管理员）  │
│      │  私聊/群/回应/评论               │  /chats /link 说做定时     │
│      ▼                                 ▼                           │
│  ┌─────────────┐   ┌──────────────────┐   ┌─────────────────────┐ │
│  │ chat 对话管线 │   │ director 控制台  │   │ panel 面板(aiohttp) │ │
│  │ (绑定对话)   │   │                  │   │ (token 鉴权+SPA)    │ │
│  └──────┬──────┘   └────────┬─────────┘   └─────────────────────┘ │
│         │    共 用 执 行 通 道 (actions)                            │
│  ┌──────▼───────────────────▼──────────────────────────────────┐  │
│  │ heart 心跳(纯代码tick) → desire 意愿 → planner/impulses      │  │
│  └──┬─────────┬─────────┬─────────┬─────────┬───────────────────┘  │
│     ▼         ▼         ▼         ▼         ▼                      │
│  persona   memory     life     events    gallery/imagegen          │
│  生命档案   四层记忆   虚拟生活  事件流    图库+生图                 │
│     │         │         │         │         │                      │
│  ┌──▼─────────▼─────────▼─────────▼─────────▼──────────────────┐  │
│  │ store: SQLite(aiosqlite) + 向量表(numpy余弦) + 文件区 data/  │  │
│  └──────────────────────────────────────────────────────────────┘  │
│  providers: LLM(OpenAI兼容)/Embedding/TTS(多引擎)/STT(Whisper兼容) │
└────────────────────────────────────────────────────────────────────┘
```

## 二、关键设计决策

### D1 绑定对话
她生活在**绑定对话**里（导演 bot `/chats` 列出、`/link <chat_id>` 绑定与切换）。
绑定对话的消息由 `chat/handler.py` 全权处理：上下文组装（档案+时间+生活状态+情绪+小抄+
召回记忆+未提及事件）→ 主模型 → 标记协议解析 → 多形态回复 → 记忆沉淀。
未绑定时，管理员私聊主 bot 自动绑定。陌生人私聊礼貌拒绝（她是"专一的"）。

### D2 回复标记协议（provider 无关）
主模型输出带轻量标记的回复，composer 解析执行：`<seg/>` 分段、`<voice>` 语音条、
`<sticker>` 表情包、`<photo>` 照片情境、`<improv>` 编造固化（A6）、`<told>/<found>`
事件提及状态。解析失败整体降级为纯文本，永不吞消息。
连发消息 2.2s 防抖合并为一轮；分段投递带 typing 与拟真延迟。

### D3 心跳：纯代码 tick + 模型按需出场（成本意识）
默认 5 分钟一 tick：推进日程、衰减情绪（P1 半衰期）、扫描到期待办、
记忆沉淀（对话空闲后）、日记/周记到点生成、主动意愿打分、生活冲动掷签、
图库 pending 打标消化。只有过阈值的决策与成文才调用模型。

### D4 事件流是拟真三支柱的枢纽（A2）
一切自主行为（换头像/改签名/发动态/主动消息）统一经 `actions.py` 执行，成功即写入
事件流（内容描述+真实动机+提及状态），成为：日记素材、可提及话题、"被发现"的应答依据。
导演编排走同一通道，故"她恰好做了你想让她做的事"。
表情回应（reactions）同样进入事件流——你点的 ❤️ 是她的开心素材。

### D5 图库与生图共用"情境需求描述"（R5）
选图不检索字面：主模型先产出下一幕的情境描述（场景/状态/情绪/构图），
再语义检索图库（打标与检索同一套语言）→ 相似度不足降级生图（同一份描述）→
生成图回流入库。外观一致性（A9）：NanoBanana 走锚点参考图，ComfyUI 走用户 workflow
内嵌 LoRA/IPAdapter，外观演变状态注入所有路径并参与检索降权。

### D6 向量检索：SQLite + numpy 余弦
个人量级（记忆几千条、图库几百张）无需向量数据库：embedding 存 SQLite BLOB，
numpy 点积毫秒级，零外部依赖，备份即复制库文件。Embedding 未配置时优雅降级为
非语义策略（最近优先/标签匹配），功能不崩。

### D7 定时任务自持久化
导演编排的"定时"与她自己的"打算"写入 `pending_actions`（due_ts+payload），
心跳扫描执行，重启天然恢复。

### D8 安全边界
- 导演 bot 只认 `admin_id`，其他人静默无视；面板必须配置 token 才启动；
- 频道评论等外部文本一律 `wrap_external()` 包裹为"她读到的内容"，绝不进指令层；
  公开回复使用不含私密记忆的精简人格提示；
- 陌生人评论限额（每人每小时 2 条、全体每天 15 条）。

## 三、数据模型（SQLite，单文件 data/loverbot.db）

| 表 | 用途 |
|----|------|
| chat_log | 自管对话历史（工作记忆底层，带 voice/photo/sticker 类型） |
| facts | 结构化事实（A1/A6，可失效） |
| diary | 日记/周记（A1，向量化召回） |
| events | 生活事件流（A2：内容/动机/提及状态） |
| schedule | 日程（A4） |
| mood | 情绪（P1 半衰期） |
| cheatsheet | 核心小抄（版本化，她自修订） |
| gallery | 图库（R4：打标/外观/锚点/使用记录） |
| pending_actions | 待执行动作（D7） |
| vectors | 向量表（D6：memory/gallery 两类） |
| seen_chats | 见过的对话（/chats 数据源） |
| kvmisc | 游标/计数器/绑定对话/参数覆盖 |

文件区：`persona/`（档案+动态层）、`gallery/files/`、`voice/`、`exports/`、`logs/`、`tmp/`。

## 四、目录结构

```
loverbot/
├── main.py                 # 入口
├── config.example.yaml
├── Dockerfile / docker-compose.yml
├── web/                    # 面板 SPA（fetch+token）
├── examples/persona.example.yaml
├── tests/
└── loverbot/
    ├── app.py              # 装配中心
    ├── config.py  log.py  security.py  actions.py
    ├── providers/          # llm / embedding / tts / stt（全部可替换）
    ├── store/              # db / dao / vectors / export
    ├── persona/            # profile / dynamic / prompt（生命档案与提示词组装）
    ├── memory/             # working / pipeline（四层记忆）
    ├── life/               # clock / engine / mood（时间感知/虚拟生活/情绪）
    ├── heart/              # heartbeat / desire / planner / impulses
    ├── chat/               # composer / delivery / handler（标记协议与投递）
    ├── gallery/  imagegen/ # 图库与生图（nanobanana/comfyui/novelai）
    ├── voice/              # TTS→ogg 语音条 / STT
    ├── tg/                 # mainbot / reactions / channel / service
    ├── director/           # bot / console / status（导演控制台）
    └── panel/              # aiohttp 面板服务
```

## 五、已识别风险与对策

1. **主模型输出标记不规范** → composer 容错解析 + 纯文本降级（D2）；
2. **Embedding/生图/语音任一缺席** → 能力开关裁剪提示词协议，她不会承诺做不到的事；
3. **频道匿名回应只有聚合数** → 以 message_reaction_count 增量记事件，具名回应单独记；
4. **换头像频率限制**（Telegram 资料修改限速）→ 天级冷却本来就是需求（R2）；
5. **单进程崩溃** → 状态全部落 SQLite/YAML，重启即恢复；docker `restart: unless-stopped`。
