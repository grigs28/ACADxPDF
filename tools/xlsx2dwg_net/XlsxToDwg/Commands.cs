using System;
using System.IO;
using System.Text;
using System.Text.Json;
using Autodesk.AutoCAD.ApplicationServices.Core;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
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
                            EntityBuilder.AddTable(tr, modelSpace, sec, xlsxDoc.DefaultTextHeight, textStyleId, tableStyleId);
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
