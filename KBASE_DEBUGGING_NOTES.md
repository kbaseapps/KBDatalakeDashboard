# KBase Deployment Debugging Notes

**Date:** 2026-02-16
**Issue:** App running 2+ hours with no log output

---

## Problem Analysis

### Current Situation
Two jobs running with no visible logs:
1. **Job 1:** commit d4a75f3 - Running 2h 12m
2. **Job 2:** commit 28bb4db - Running 54m 46s

Both jobs show:
- ✅ Job started
- ✅ Docker container running
- ❌ NO application logs visible

### Root Cause Hypotheses

#### Hypothesis #1: Python stdout buffering (MOST LIKELY)
- Python buffers stdout by default
- `print()` statements don't flush immediately
- Logs may be buffered and not visible until process completes or buffer fills

**Fix Applied (commit 99e55c7):**
- Added `flush=True` to all print statements
- Added `sys.stdout.flush()` after critical blocks
- Next deployment should show logs immediately

#### Hypothesis #2: Shock upload hanging (VERY LIKELY)
- Lines 121-135 in KBDatalakeDashboardImpl.py upload HTML directory to Shock
- Directory contains:
  - Dashboard HTML/JS/CSS
  - Heatmap viewer HTML/JS/CSS
  - Large JSON data files (genes_data.json ~570KB, cluster_data.json ~235KB, reactions_data.json, metabolic maps 1-2MB)
  - Total directory size: Unknown (added check in commit 99e55c7)

**Potential issues:**
- Zipping large directory takes time
- Shock upload over network can be slow
- No timeout configured
- No progress indication

**Next deployment will show:**
```
Directory size to upload: XXX MB
Uploading HTML directory to Shock...
```

This will tell us if size is the problem.

#### Hypothesis #3: Data file generation within Docker
- The HTML viewers expect data files (genes_data.json, etc.) but these may not be in the Docker image
- Current .gitignore excludes data files from KBase repo
- Viewers might be trying to load non-existent files

**Check:** Do the data files exist in `/kb/module/data/html/` and `/kb/module/data/heatmap/` within the Docker container?

---

## Commits Timeline

### Commit d4a75f3 (2026-02-16 23:56)
- Added KBDatalakeDashboard2 section to deploy.cfg
- Fixed config parsing error

### Commit 28bb4db (2026-02-17 03:48)
- Added extensive print logging
- **But prints were not flushing!**

### Commit 99e55c7 (2026-02-17 04:03) ✅ LATEST
- Added flush=True to all print statements
- Added sys.stdout.flush() calls
- Added directory size check before Shock upload
- **This should fix the log visibility issue**

---

## Next Steps

### Immediate: Wait for Next Deployment
The kbaseapps/KBDatalakeDashboard repo now has commit 99e55c7 with flushed prints.

**To deploy:**
1. Stop current running jobs (if still hung)
2. Re-run "Run Genome Datalake Dashboard" app
3. Watch logs for:
   ```
   ================================================================================
   START: run_genome_datalake_dashboard
   Params: {...}
   ================================================================================
   Validating parameters...
   Parameters validated successfully
   Workspace: ...
   Creating output directory...
   Output directory: /path/to/dir
   Copying HTML directory from /kb/module/data/html...
   ```

**If logs appear:** Progress! We can see where it's hanging.

**If logs still don't appear:** Deeper issue with logging infrastructure.

### If Shock Upload is the Issue

**Option 1: Reduce upload size**
- Exclude large data files from Shock upload
- Use dynamic data loading from Workspace instead
- Only upload viewer HTML/CSS/JS (much smaller)

**Option 2: Add timeout**
```python
import signal

def timeout_handler(signum, frame):
    raise TimeoutError("Shock upload timed out")

signal.signal(signal.SIGALRM, timeout_handler)
signal.alarm(600)  # 10 minute timeout

try:
    shock_id = self.dfu.file_to_shock({...})
except TimeoutError:
    print("Shock upload timed out! Directory may be too large.")
finally:
    signal.alarm(0)
```

**Option 3: Stream upload with progress**
- Use chunked upload
- Report progress every N seconds

### If Data Files Are Missing

**Problem:** Viewers expect static JSON files but they're .gitignored

**Solution:** Generate data files in Docker container or load from Workspace

**Current architecture:**
- Dashboard: `/kb/module/data/html/` - expects data files here
- Heatmap: `/kb/module/data/heatmap/` - expects data files here

**Options:**
1. Include data files in Docker image (violates .gitignore)
2. Generate data files during app execution (requires Python scripts + database in Docker)
3. Use dynamic Workspace loading (requires kbase-data-loader.js to work)

**Recommended:** Option 3 - dynamic Workspace loading
- Heatmap viewer already has `kbase-data-loader.js`
- Should load data from Workspace object UPA
- Check if it's actually working

---

## Multi-Genome Support (Deferred Items)

You mentioned we planned features that haven't been implemented:

### Planned But Not Implemented
1. **Multi-genome comparison**
   - Display multiple genomes side-by-side in tracks
   - Comparative heatmaps
   - Synteny visualization

2. **Enhanced reports**
   - Multiple report types
   - Export formats (PDF, Excel)
   - Customizable templates

3. **Additional visualizations**
   - Genome browser
   - Pathway enrichment
   - More interactive plots

### Why Deferred
We've been focused on getting the basic single-genome app working in KBase. Once the deployment issues are resolved, we can revisit these features.

---

## Chris Henry's Tools

### kb_sdk_plus (alpha)
- Modernized kb-sdk with better CLI
- Worth trying if current kb-sdk has issues
- https://github.com/kbase/kb_sdk_plus/releases/tag/0.1.0-alpha6

### ClaudeCommands
- KBase development context for Claude
- Useful patterns and references
- https://github.com/cshenry/ClaudeCommands

### KBUtilLib
- Shared utility library for KBase apps
- Avoid code duplication
- https://github.com/cshenry/KBUtilLib

**Recommendation:** Consider using KBUtilLib for workspace operations and report generation in future refactoring.

---

## Immediate Action Plan

**For the hung jobs:**
1. ✅ Pushed commit 99e55c7 with flushed prints to kbaseapps repo
2. ⏳ Wait for next deployment or manually re-run app
3. 👀 Watch for logs to appear
4. 🔍 If logs appear, identify where it's hanging (likely Shock upload)
5. 🛠️ Implement fix based on findings

**Expected timeline:**
- Next deployment: Immediate (just re-run the app)
- Logs should appear: Within first 10 seconds
- Identify bottleneck: Within first minute
- Implement fix: Depends on root cause

---

## Summary

**What we know:**
- App starts successfully
- Docker container runs
- No logs visible (Python buffering issue)

**What we fixed:**
- Added stdout flushing (commit 99e55c7)
- Added directory size check

**What we suspect:**
- Shock upload of large directory is hanging (2+ hours)
- Solution: Reduce upload size or add timeout

**Next test:**
- Re-run app with commit 99e55c7
- Logs should now appear
- Will reveal actual bottleneck
