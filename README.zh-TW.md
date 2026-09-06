# agent-library

**讓多個 AI agent 共用一座可追溯、可治理、逐層閱讀的文件圖書館。**

[English](README.md) · [架構](docs/architecture.md) · [治理規則](docs/governance.md) · [踩坑與驗證對照](docs/failure-modes.md)

[產品共識與發展邊界](docs/product-spec.md)：完整產品面向醫院檢驗科及 IVD
公司的品質管理；目前實作文件圖書館核心。去識別化引擎獨立管理，
透過公開介面整合；院內病人資料與受控雲端分析是後續階段。

既有資料庫可先用 [唯讀清冊稽核](docs/inventory-audit.md)，找出同名衝突、
缺少文字側車及未驗證內容；工具不會把同雜湊檔案自動刪除或合併。

每一份原檔的每一個版本，都保留三種不同用途的內容：

1. **原檔**：保留 PDF／Word 的原始位元組，提供查證與交付。
2. **全文 Markdown**：完整保存解析器回傳的各頁文字，保留頁面位置，不用摘要替代全文。
3. **重點 Markdown**：協助找路；長文件再拆成下一層重點，連回同一版本的全文頁面。

Agent 的路徑是：**治理規則 → 總目錄 → 分類目錄 → 文件重點 → 章節重點 → 全文相關頁 → 原檔**。不需要一開始把所有文件塞進上下文，也不需要向量庫。

## 第一版已經能做什麼

| 項目 | 狀態 |
| --- | --- |
| 本機來源白名單、檔案穩定後收件、重複執行去重 | 已實作 |
| PDF 解析；Word 經 LibreOffice 轉換後解析 | 已實作，提供合成檔實測腳本 |
| 全文與獨立重點檔、長文件與大目錄分層 | 已實作；重點目前採原文摘錄導覽 |
| 審閱、發布、取代舊版、封存、還原、稽核 | 已實作；單機操作人模式 |
| 原檔／全文／重點／抽取結果一起驗證 | 已實作 |
| NAS 正式部署、Google Drive 自動傳輸 | 下一階段；目前可匯出與驗證可攜發布包 |
| LangExtract 或模型產生語意重點 | 下一階段；必須附上來源位置 |

這是可運行的開源核心，尚未宣稱已接管任何公司的 NAS 或雲端資料庫。

## 直接試跑

需要 Python 3.11+。在原始碼 checkout 中執行：

```sh
python -m pip install .
python examples/demo.py
python -m unittest discover -s tests -v
```

示範會建立合成文件，實際走完發布、換版、封存、還原與發布包驗證。若要保留結果供 Obsidian 閱讀：

```sh
python examples/demo.py --output ../agent-library-demo
```

開啟產出資料夾中的 `bundle/_AI_MAP.md`，就能順著連結往下讀。輸出位置必須是專案目錄外的新資料夾。

PDF 驗證需先安裝 LiteParse 2.0.0；Word 另需 LibreOffice：

```sh
npm install -g @llamaindex/liteparse@2.0.0
python examples/verify_liteparse.py
python examples/verify_liteparse.py --office
```

Windows PowerShell 可用 `npm.cmd`。正式文件操作方式見 [英文快速開始](README.md#operating-your-own-private-library)。

## 幾個刻意分開的觀念

**檔案存在、文字抽取完成、操作人批准發布、內容對外有效，是四件不同的事。** 非空 Markdown 可能只有檔名或 OCR 佔位文字，因此不能拿檔案大小判定品質。`extracted` 也只代表每一頁都有解析文字，不能證明每個數字、表格與圖片都正確。

**摘要找路，全文查證。** 第一版的重點是短原文摘錄，不會自行推導效期、補寫缺漏或改寫原始條文。未來接入語意重點生成器時，重點必須綁定原檔雜湊、全文雜湊與頁面／字元範圍。

**Agent 提出變更，治理程式執行約束。** 版本切換先產生具體計畫，批准必須對應同一份計畫雜湊。若原檔、規則或目前版本已改變，執行會停止。第一版的審閱者名稱只是本機稽核標籤，正式多人部署還需要獨立身分驗證及權限分離。

**NAS 檔案不見了，不等於應該刪掉圖書館內容。** 來源離線、權限錯誤、同步中斷會被記錄；已保存的版本保留，封存亦不永久刪除。原始業務資料應留在私有資料區，公開 GitHub 只放程式、規範及合成範例。

下一階段依照 [部署路線](docs/roadmap.md)，先建立實際來源清冊，再做少量文件的 NAS → 發布包 → Drive 回讀驗證。
