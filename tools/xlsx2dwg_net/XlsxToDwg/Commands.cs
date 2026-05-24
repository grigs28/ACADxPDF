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

            Log($"sections={xlsxDoc.Sections.Count}");
            int mtextCount = 0, tableCount = 0, errorCount = 0;

            using (var tr = db.TransactionManager.StartTransaction())
            {
                var textStyleId = StyleSetup.EnsureTextStyle(tr, db, xlsxDoc.Font);
                var boldTextStyleId = StyleSetup.EnsureBoldTextStyle(tr, db, xlsxDoc.Font);
                var tableStyleId = StyleSetup.EnsureTableStyle(tr, db, textStyleId, xlsxDoc.DefaultTextHeight);

                var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
                var modelSpace = (BlockTableRecord)tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForWrite);

                foreach (var sec in xlsxDoc.Sections)
                {
                    try
                    {
                        if (sec.Type == "mtext")
                        {
                            EntityBuilder.AddMText(tr, modelSpace, sec, xlsxDoc.DefaultTextHeight, textStyleId);
                            mtextCount++;
                        }
                        else if (sec.Type == "table")
                        {
                            EntityBuilder.AddTable(tr, modelSpace, sec, xlsxDoc.DefaultTextHeight, textStyleId, boldTextStyleId, tableStyleId);
                            tableCount++;
                        }
                    }
                    catch (System.Exception ex)
                    {
                        Log($"ERR [{sec.Type}]: {ex.Message}");
                        errorCount++;
                    }
                }

                tr.Commit();
                Log($"OK: {mtextCount} mtext, {tableCount} table, {errorCount} errors");

                // 多页图框复制
                if (xlsxDoc.NumPages > 1)
                {
                    using (var tr2 = db.TransactionManager.StartTransaction())
                    {
                        var bt2 = (BlockTable)tr2.GetObject(db.BlockTableId, OpenMode.ForRead);
                        var ms2 = (BlockTableRecord)tr2.GetObject(bt2[BlockTableRecord.ModelSpace], OpenMode.ForWrite);

                        // 收集模板原有实体（在 0,0 附近的）
                        var templateIds = new List<ObjectId>();
                        foreach (ObjectId id in ms2)
                        {
                            var ent = (Entity)tr2.GetObject(id, OpenMode.ForRead);
                            if (ent is Table || ent is MText)
                                continue;
                            var ext = ent.GeometricExtents;
                            if (ext.MinPoint.X < 100 && ext.MaxPoint.X < 90000
                                && ext.MinPoint.Y > -100 && ext.MaxPoint.Y < 60000)
                            {
                                templateIds.Add(id);
                            }
                        }

                        // 对 page 1..N-1 复制图框
                        for (int pg = 1; pg < xlsxDoc.NumPages; pg++)
                        {
                            double offsetX = pg * (84100.0 + 2000.0);
                            foreach (ObjectId tid in templateIds)
                            {
                                var srcEnt = (Entity)tr2.GetObject(tid, OpenMode.ForRead);
                                var cloned = (Entity)srcEnt.Clone();
                                var mat = Matrix3d.Displacement(new Vector3d(offsetX, 0, 0));
                                cloned.TransformBy(mat);
                                ms2.AppendEntity(cloned);
                                tr2.AddNewlyCreatedDBObject(cloned, true);
                            }
                        }
                        tr2.Commit();
                        Log($"FRAME: duplicated for {xlsxDoc.NumPages} pages");
                    }
                }
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
}
