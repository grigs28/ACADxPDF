using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.Json;
using Autodesk.AutoCAD.ApplicationServices.Core;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.Runtime;

[assembly: CommandClass(typeof(XlsxToDwg.Commands))]

namespace XlsxToDwg;

public class Commands
{
    private static void Log(string msg)
    {
        try
        {
            var path = Path.Combine(Path.GetTempPath(), "xlsx2dwg_cs.log");
            File.AppendAllText(path, $"[{DateTime.Now:HH:mm:ss.fff}] {msg}\n", Encoding.UTF8);
        }
        catch { }
    }

    [CommandMethod("XLSX2DWG")]
    public static void XlsxToDwgCommand()
    {
        Log("START");
        var doc = Application.DocumentManager.MdiActiveDocument;
        var ed = doc.Editor;
        var db = doc.Database;

        try
        {
            var getPath = ed.GetString("\nJSON path: ");
            if (getPath.Status != PromptStatus.OK) return;
            string jsonPath = getPath.StringResult.Trim();
            if (!File.Exists(jsonPath)) { Log($"NOT FOUND: {jsonPath}"); return; }

            string json = File.ReadAllText(jsonPath, Encoding.UTF8);
            var xlsxDoc = JsonSerializer.Deserialize<XlsxDocument>(json);
            if (xlsxDoc == null || xlsxDoc.Sections.Count == 0) { Log("EMPTY"); return; }

            Log($"version={xlsxDoc.Version}, sections={xlsxDoc.Sections.Count}");

            using (var tr = db.TransactionManager.StartTransaction())
            {
                var textStyleId = StyleSetup.EnsureTextStyle(tr, db, xlsxDoc.Font);
                var boldTextStyleId = StyleSetup.EnsureBoldTextStyle(tr, db, xlsxDoc.Font);
                var tableStyleId = StyleSetup.EnsureTableStyle(tr, db, textStyleId, xlsxDoc.DefaultTextHeight);

                // ── Pass 1: 测量行高 ──
                Log("Pass 1: BuildMegaTable + MeasureRowHeights");
                var (megaTable, rowIsText, maxNcols) = TableBuilder.BuildMegaTable(
                    xlsxDoc, tableStyleId, textStyleId, boldTextStyleId, xlsxDoc.DefaultTextHeight);
                var measuredHeights = TableBuilder.MeasureRowHeights(megaTable, megaTable.Rows.Count);

                for (int i = 0; i < Math.Min(measuredHeights.Count, 10); i++)
                    Log($"  Row {i}: height={measuredHeights[i]:F1}");

                // 文本行高 ×1.5 增加间距
                for (int i = 0; i < measuredHeights.Count; i++)
                    if (rowIsText[i])
                        measuredHeights[i] *= 1.5;

                // ── Pass 2: 切分 + 布局 ──
                Log("Pass 2: SplitIntoChunks + CreateTables");

                // 构建全局列宽（缩放到 COL_W）
                var colWidths = TableBuilder.BuildGlobalColWidths(xlsxDoc, maxNcols);

                // 切分
                var chunks = TableBuilder.SplitIntoChunks(measuredHeights, Layout.MAX_COL_H);
                Log($"  Chunks: {chunks.Count}");

                // 收集所有 cells 和 merges（从 xlsxDoc 重新构建，和 BuildMegaTable 相同逻辑）
                // 同时构建段边界映射
                var allCells = new List<CellInfo>();
                var allMerges = new List<MergeInfo>();
                // sectionBoundaries: (startRow, endRow, headerNRows) per section
                var sectionBounds = new List<(int startRow, int endRow, int headerNRows)>();
                int rowOffset = 0;
                foreach (var sec in xlsxDoc.Sections)
                {
                    int secStart = rowOffset;
                    if (sec.Borderless)
                    {
                        for (int ri = 0; ri < sec.NRows; ri++)
                        {
                            var matching = new List<CellInfo>();
                            foreach (var c in sec.Cells) if (c.Row == ri) matching.Add(c);
                            if (matching.Count > 0)
                            {
                                var c = matching[0];
                                allCells.Add(new CellInfo { Row = rowOffset + ri, Col = 0, Text = c.Text, TextHeight = c.TextHeight, Alignment = c.Alignment, Bold = c.Bold });
                                allMerges.Add(new MergeInfo { R1 = rowOffset + ri, C1 = 0, R2 = rowOffset + ri, C2 = maxNcols - 1 });
                            }
                        }
                        sectionBounds.Add((secStart, rowOffset + sec.NRows - 1, 0));
                    }
                    else
                    {
                        foreach (var c in sec.Cells)
                            allCells.Add(new CellInfo { Row = c.Row + rowOffset, Col = c.Col, Text = c.Text, TextHeight = c.TextHeight > 0 ? c.TextHeight : xlsxDoc.DefaultTextHeight, Alignment = c.Alignment, Bold = c.Bold, Borders = c.Borders });
                        foreach (var m in sec.Merges)
                            allMerges.Add(new MergeInfo { R1 = m.R1 + rowOffset, C1 = m.C1, R2 = m.R2 + rowOffset, C2 = m.C2 });
                        sectionBounds.Add((secStart, rowOffset + sec.NRows - 1, sec.HeaderNRows));
                    }
                    rowOffset += sec.NRows;
                }

                // 贪心装箱：3 列/页
                var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
                var modelSpace = (BlockTableRecord)tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForWrite);

                int pageIndex = 0;
                int colIdx = 0;
                double colY = Layout.CONTENT_TOP - Layout.CONTENT_START_OFFSET;
                int tableCount = 0;

                foreach (var (startRow, endRow) in chunks)
                {
                    // 查找此 chunk 起始行所在的段，确定是否需要前置表头
                    int headerStartRow = -1, headerEndRow = -1;
                    foreach (var (secStart, secEnd, secHeaderNRows) in sectionBounds)
                    {
                        if (startRow >= secStart && startRow <= secEnd && secHeaderNRows > 0)
                        {
                            // 不在段首时需要重复表头
                            if (startRow > secStart + secHeaderNRows - 1)
                            {
                                headerStartRow = secStart;
                                headerEndRow = secStart + secHeaderNRows - 1;
                            }
                            break;
                        }
                    }

                    // 计算 chunk 高度（含表头）
                    int headerNRows = (headerStartRow >= 0) ? (headerEndRow - headerStartRow + 1) : 0;
                    double chunkH = 0;
                    for (int r = startRow; r <= endRow; r++)
                        chunkH += r < measuredHeights.Count ? measuredHeights[r] : 300;
                    for (int r = headerStartRow; r <= headerEndRow; r++)
                        chunkH += (r >= 0 && r < measuredHeights.Count) ? measuredHeights[r] : 300;
                    chunkH = Math.Max(chunkH, Layout.H_BODY);

                    double remaining = colY - Layout.DRAW_BOTTOM;
                    bool atColumnTop = (colY >= Layout.CONTENT_TOP - Layout.CONTENT_START_OFFSET - 1);
                    if (!atColumnTop && chunkH > remaining)
                    {
                        colIdx++;
                        if (colIdx >= Layout.COL_COUNT)
                        {
                            pageIndex++;
                            colIdx = 0;
                        }
                        colY = Layout.CONTENT_TOP - Layout.CONTENT_START_OFFSET;
                    }

                    double pageX = pageIndex * (Layout.FRAME_W + Layout.FRAME_GAP);
                    double colX = Layout.DRAW_LEFT - Layout.COL_LEFT_PAD + colIdx * (Layout.COL_W + Layout.COL_GAP);
                    double insertX = pageX + colX;
                    double insertY = colY;

                    var insertPt = new Point3d(insertX, insertY, 0.0);

                    Log($"  chunk: rows {startRow}-{endRow} hdr {headerStartRow}-{headerEndRow} h={chunkH:F0} rem={remaining:F0} → page{pageIndex} col{colIdx} y={insertY:F0}");

                    EntityBuilder.AddTableFromChunk(
                        tr, modelSpace, tableStyleId, textStyleId, boldTextStyleId,
                        xlsxDoc.DefaultTextHeight,
                        colWidths,
                        startRow, endRow,
                        headerStartRow, headerEndRow,
                        measuredHeights, allCells, allMerges, rowIsText, maxNcols,
                        insertPt);

                    tableCount++;
                    colY -= chunkH;
                }

                // 添加每页标题（MText 居中）
                int numPages = pageIndex + 1;
                for (int pi = 0; pi < numPages; pi++)
                {
                    double px = pi * (Layout.FRAME_W + Layout.FRAME_GAP);
                    string cn = pi < Layout.CN_NUMS.Length ? Layout.CN_NUMS[pi] : (pi + 1).ToString();
                    double titleX = px + Layout.DRAW_LEFT + Layout.DRAW_W / 2;
                    double titleY = Layout.DRAW_TOP - Layout.TITLE_Y_OFFSET;

                    var titleSec = new SectionInfo
                    {
                        Type = "mtext",
                        Text = $"绿色建筑设计专篇（建筑）{cn}",
                        Insert = new List<double> { titleX, titleY, 0.0 },
                        Width = Layout.DRAW_W,
                        TextHeight = Layout.H_TITLE,
                        Bold = true,
                        Attachment = "TopCenter"
                    };
                    EntityBuilder.AddMText(tr, modelSpace, titleSec, xlsxDoc.DefaultTextHeight, textStyleId);
                }

                // 多页图框复制
                if (numPages > 1)
                {
                    var templateIds = new List<ObjectId>();
                    foreach (ObjectId id in modelSpace)
                    {
                        var ent = (Entity)tr.GetObject(id, OpenMode.ForRead);
                        if (ent is Table || ent is MText)
                            continue;
                        var ext = ent.GeometricExtents;
                        if (ext.MinPoint.X < 100 && ext.MaxPoint.X < 90000
                            && ext.MinPoint.Y > -100 && ext.MaxPoint.Y < 60000)
                        {
                            templateIds.Add(id);
                        }
                    }

                    for (int pg = 1; pg < numPages; pg++)
                    {
                        double offsetX = pg * (Layout.FRAME_W + Layout.FRAME_GAP);
                        foreach (ObjectId tid in templateIds)
                        {
                            var srcEnt = (Entity)tr.GetObject(tid, OpenMode.ForRead);
                            var cloned = (Entity)srcEnt.Clone();
                            var mat = Matrix3d.Displacement(new Vector3d(offsetX, 0, 0));
                            cloned.TransformBy(mat);
                            modelSpace.AppendEntity(cloned);
                            tr.AddNewlyCreatedDBObject(cloned, true);
                        }
                    }
                    Log($"FRAME: duplicated for {numPages} pages");
                }

                // Dispose 测量用 mega-Table
                megaTable.Dispose();

                tr.Commit();
                Log($"OK: {tableCount} tables, {numPages} pages");
            }
        }
        catch (System.Exception ex)
        {
            Log($"FATAL: {ex.Message}");
        }
    }

    [CommandMethod("XLSXHELLO")]
    public static void Hello()
    {
        var doc = Application.DocumentManager.MdiActiveDocument;
        doc.Editor.WriteMessage("\n[XlsxToDwg] DLL loaded successfully!");
    }

    [CommandMethod("XLSXTEST")]
    public static void XlsxTest()
    {
        Log("XLSXTEST START");
        var doc = Application.DocumentManager.MdiActiveDocument;
        var db = doc.Database;

        try
        {
            using (var tr = db.TransactionManager.StartTransaction())
            {
                var font = new FontInfo { Name = "宋体", Charset = 134, PitchAndFamily = 2 };
                var textStyleId = StyleSetup.EnsureTextStyle(tr, db, font);
                var tableStyleId = StyleSetup.EnsureTableStyle(tr, db, textStyleId, 300);

                var table = new Table();
                table.TableStyle = tableStyleId;
                table.SuppressRegenerateTable(true);
                table.IsTitleSuppressed = true;
                table.SetSize(5, 3);

                // 列宽
                table.Columns[0].Width = 8000;
                table.Columns[1].Width = 8000;
                table.Columns[2].Width = 8000;

                // 显式行高（后两行）
                table.Rows[3].Height = 600;
                table.Rows[4].Height = 800;

                // 填入内容
                table.Cells[0, 0].TextString = "一二三四五六七八九十";
                table.Cells[1, 0].TextString = "这是一段很长的中文文本用来测试自动换行功能是否正常工作需要很多字符";
                table.Cells[2, 1].TextString = "Short text";
                table.Cells[3, 0].TextString = "显式高度600";
                table.Cells[4, 0].TextString = "显式高度800";

                // 所有 cell 设文字高度和对齐
                for (int r = 0; r < 5; r++)
                {
                    for (int c = 0; c < 3; c++)
                    {
                        table.Cells[r, c].TextHeight = 300;
                        table.Cells[r, c].Alignment = CellAlignment.MiddleLeft;
                    }
                }

                table.GenerateLayout();
                table.SuppressRegenerateTable(false);

                // 读取并 Log 每行高度
                for (int r = 0; r < 5; r++)
                {
                    double h = table.Rows[r].Height;
                    Log($"Row {r}: Height = {h}");
                }

                table.Dispose();
                tr.Commit();
                Log("XLSXTEST OK");
            }
        }
        catch (System.Exception ex)
        {
            Log($"XLSXTEST FATAL: {ex.Message}");
        }
    }
}
