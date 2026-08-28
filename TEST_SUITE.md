# Mark Test Suite & Examples
## Verify All Features Working Correctly

---

## ✅ Pre-Launch Checklist

Before you start Mark, verify:

- [ ] **Python 3.12 installed**
  ```powershell
  python.exe --version
  # Should show: Python 3.12.x
  ```

- [ ] **All dependencies installed**
  ```powershell
  pip list | findstr -E "Flask|SpeechRecognition|pyttsx3|pytesseract"
  # Should show: Flask, SpeechRecognition, pyttsx3, pytesseract
  ```

- [ ] **Tesseract OCR available**
  ```powershell
  "C:\Program Files\Tesseract-OCR\tesseract.exe" --version
  # Should show: tesseract v5.x.x
  ```

- [ ] **commands.json exists**
  ```powershell
  Test-Path "c:\Users\MAYUR DAVE\OneDrive\Desktop\M\Secret\JARVIS\commands.json"
  # Should show: True
  ```

- [ ] **Google API credentials configured** (if using Google Sheets)
  ```powershell
  $env:GOOGLE_CREDENTIALS_PATH
  # Should show path to credentials.json
  ```

---

## 🚀 Launch Test

```powershell
cd 'c:\Users\MAYUR DAVE\OneDrive\Desktop\M\Secret\JARVIS'
python.exe Mark.py
```

**Expected output:**
```
[System initializing...]
Mark Assistant initialized
Dashboard started on port 8080
Mark listening...
```

**If you see an error:**
- Check that all dependencies are installed (`pip install -r requirements.txt`)
- Verify Python path is correct
- Check that port 8080 is not in use (nothing else running)

---

## 🎯 Feature Test Suite

### Test 1: Dashboard Access
```
Step 1: Open browser to http://localhost:8080
Step 2: You should see Mark dashboard with:
  ✓ "Mark" title
  ✓ System status panel
  ✓ Control buttons (Read Screen, Pause, Resume, Shutdown)
  ✓ Lead summary
  ✓ Events log
```

**Expected:**
- Dashboard loads instantly
- Status shows "Listening"
- Auto-refreshes every 3 seconds

---

### Test 2: Fuzzy Matching (Voice Commands)

**Scenario A: Perfect match**
```
User says: "Read screen"
Expected:
  - Mark recognizes immediately
  - Confidence: 95%+
  - Reads visible screen text
```

**Scenario B: Typo tolerance**
```
User says: "red screan" (missing 'a', typo in 'screen')
Expected:
  - Mark still recognizes as "read screen"
  - Confidence: 85-90% (lower but still executes)
  - Reads screen successfully
```

**Scenario C: Word order variation**
```
User says: "developers React find" (reversed word order)
Expected:
  - Mark interprets as lead generation intent
  - Confidence: 75-85%
  - Searches for React developers
```

**Scenario D: Low confidence (should reject)**
```
User says: "xyz random blah words"
Expected:
  - Mark: "I'm not confident about that command"
  - Does NOT execute anything
  - Asks you to repeat
```

---

### Test 3: Confidence Scoring

**Check dashboard during commands:**

1. **Say:** "Find leads for Python developers"
   - Dashboard should show:
     ```
     Heard: find leads for python developers
     Intent: lead_generation
     Confidence: 88%  ← This is the confidence score
     Mode: action
     ```

2. **Say:** "Read this screen" (slight variation)
   - Confidence might be: 92% (close to trained phrase)
   - Should still execute (>65% threshold)

3. **Say:** "Blah blah blah" (nonsense)
   - Confidence should be: 35-45% (<65% threshold)
   - Mark should ask you to repeat

---

### Test 4: Context Tracking

**Verify conversation history:**

1. Say several commands in sequence:
   - "Find leads for React"
   - "Show me the first result"
   - "Create a proposal"

2. Check dashboard:
   - Recent queries should show all 3
   - Intents should be logged
   - Confidence scores for each

---

### Test 5: Lead Quality Filtering

**Test lead search:**

```
Step 1: Say "Find leads for Nodejs developers"
Step 2: Check dashboard for results
Step 3: Verify quality:
  ✓ All results have meaningful descriptions
  ✓ No generic/spam results
  ✓ Results sorted by quality (longest descriptions first)
  ✓ Each snippet is >10 characters
```

**Expected metrics:**
- Spam filtered: 100% (no junk results)
- Average snippet length: >25 characters
- Results per search: 10 (or fewer if quality filtering reduced results)

---

### Test 6: Proposal Generation

**Test arbitrage proposal:**

```
Step 1: Say "Create arbitrage proposal"
Step 2: Wait for Mark to generate proposal
Step 3: Verify proposal includes:
  ✓ TALENT ARBITRAGE PROPOSAL header
  ✓ Client name (from search results)
  ✓ Lead source URL
  ✓ Current date
  ✓ EXECUTIVE SUMMARY section
  ✓ TALENT MATCH section
  ✓ 3 COMMERCIAL TERMS options
  ✓ VALUE PROPOSITION bullets
  ✓ WHY THIS WORKS explanation
  ✓ NEXT STEPS (5 clear steps)
```

**Quality check:**
- Professional formatting (lines, dashes)
- All sections populated
- No placeholder text remaining
- Ready to send to client

---

## 🔍 Detailed Test Cases

### Test Case 1: Accuracy with Typos
```
Test Input: "pauz listning" (typo: "pause listening")
Expected Confidence: 75-85%
Expected Action: Execute (pause_listening_agent)
Actual Confidence: ___
Actual Result: ✓ / ✗
```

### Test Case 2: Multiple Intents
```
Test Input: "find leads for urgent react developer" 
Expected Primary Intent: lead_generation OR arbitrage_lead_generation
Expected Confidence: >80%
Actual Intent: ___
Actual Confidence: ___
```

### Test Case 3: Language Switching
```
Test Input: "लीड खोजो" (Hindi: "find leads")
Expected Intent: lead_generation
Expected Confidence: >80%
Actual Result: ✓ / ✗
```

### Test Case 4: Edge Case - Acronyms
```
Test Input: "find R.D." (could mean React Developer)
Expected Confidence: <65% (too ambiguous)
Expected Action: Ask for clarification
Actual Result: ✓ / ✗
```

### Test Case 5: Confidence Below Threshold
```
Test Input: "What is the weather" (unrelated command)
Expected Confidence: <65%
Expected Action: "I'm not confident about that command"
Actual Result: ✓ / ✗
```

---

## 📊 Performance Benchmarks

After running the test suite, you should see:

| Metric | Target | Your Result |
|--------|--------|------------|
| Dashboard load time | <1 second | ___ |
| Voice recognition time | <3 seconds | ___ |
| Accuracy with perfect phrase | >95% | ___ |
| Accuracy with typos | >80% | ___ |
| False positive rate | <5% | ___ |
| Lead quality score | >80% | ___ |
| Proposal generation time | <5 seconds | ___ |

---

## 🐛 Troubleshooting Tests

### If confidence scores seem wrong:

**Test:** Voice "read screen" with intentional variations
```
1. "read screen" (perfect)
   - Confidence should be: >95%
   
2. "read screan" (typo)
   - Confidence should be: 85-90%
   
3. "rad scren" (2 typos)
   - Confidence should be: 70-80%
   
4. "xyz" (completely wrong)
   - Confidence should be: <65%
```

If scoring doesn't follow this pattern, adjust in Mark.py:
```python
# Line ~87
self.intent_confidence_threshold = 0.65  # Try 0.55 or 0.75
```

---

### If leads have poor quality:

**Test:** Check if filtering is working
```
1. Search for broad term: "developer jobs"
2. Verify results:
   - Do results have 20+ character descriptions? YES/NO
   - Are there any Google or spam results? YES/NO
   - Are results sorted longest-first? YES/NO
```

If filtering not working:
```python
# Check Mark.py search_leads() method
# Minimum snippet length currently set to: 10 characters
# Try increasing to: 15-20 characters
```

---

## ✅ Final Verification

Once all tests pass, you should see:

```
✓ Mark imports successfully
✓ Dashboard accessible at http://localhost:8080
✓ Voice commands recognized >90% accuracy
✓ Confidence scoring working correctly
✓ Fuzzy matching handles typos
✓ Context tracking active (50-query history)
✓ Leads filtered for quality
✓ Proposals generated professionally
✓ No errors in terminal output
✓ Exit code 0 (clean shutdown)
```

---

## 🚀 Ready to Go!

If all checks pass, Mark is ready for:
- ✅ 24/7 lead generation
- ✅ Arbitrage proposal creation
- ✅ Multi-language support
- ✅ High-accuracy intent detection
- ✅ Professional client communications

**Next Step:** Start generating leads and tracking earnings! 💰

---

## 📝 Test Log

Keep a record of your test results:

| Test | Date | Passed? | Notes |
|------|------|---------|-------|
| Dashboard Access | ___ | ✓/✗ | ___ |
| Fuzzy Matching | ___ | ✓/✗ | ___ |
| Confidence Scoring | ___ | ✓/✗ | ___ |
| Context Tracking | ___ | ✓/✗ | ___ |
| Lead Filtering | ___ | ✓/✗ | ___ |
| Proposal Gen | ___ | ✓/✗ | ___ |

---

**Good luck! Start Mark and begin generating leads today.** 🎯💰
