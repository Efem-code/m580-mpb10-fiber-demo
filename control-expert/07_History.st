(* =====================================================================================
   Section S07_History   (MAST task, after S06_Alarms: it uses the clock S06 reads)
   A historian inside the PLC: one sample a minute, kept for 3 days (4320 samples),
   the oldest overwritten. The dashboard, or the TV app, copies it off and keeps it.

   Each sample, 8 INTs (aHistBuf[slot, 0..7]):
     [0] date MMDD (BCD)   [1] time HHMM (BCD)
     [2] vibration X  [3] Y  [4] Z: the highest in the minute, mm/s x 100
     [5] temperature at the end of the minute, degC x 10
         (-32768 in [2]..[5]: the MPB10 sent nothing valid in that minute)
     [6] drive speed at the end of the minute, rpm (signed)
     [7] low byte: what happened at any time in the minute
           bit 0 vibration warning   bit 1 vibration alarm   bit 2 temperature warning
           bit 3 shock   bit 4 MPB10 data lost   bit 5 drive running   bit 6 drive fault
         high byte: seconds (BCD), for periods shorter than a minute

   Reading it over Modbus (one read of 124 words at %MW1900 gets it all):
     aHistHdr AT %MW1900 [0..9]
       [0] next slot to write   [1] samples kept (up to 4320)   [2] seconds per sample
       [3] samples taken so far (counts to 29999, then from 0)   [4] year of the newest sample (BCD)
       [5] slots (4320)   [6] first slot in the window   [7] request number answered
       [8] samples in the window (14)   [9] layout version (1)
     aHistReq AT %MW1910 [0..1]: write [0] first slot, [1] a new request number.
       The PLC copies 14 samples from that slot into aHistWin, then puts the slot in
       aHistHdr[6] and the request number in aHistHdr[7]. [0] = -1 clears the history.
     aHistWin AT %MW1912 [0..111]: the 14 samples asked for, 8 words each.

   The buffer is unlocated, so it is kept through a warm start (power cut) and
   cleared by a cold start or a download.
   ===================================================================================== *)

(* ---- what is happening now ---- *)
wHistNow := 0;
wHistNow.0 := xVibWarning;
wHistNow.1 := xVibAlarm;
wHistNow.2 := xTempWarning;
wHistNow.3 := xShock;
wHistNow.4 := NOT xMPB_DataOK;
wHistNow.5 := xATV_Running;
wHistNow.6 := xATV_Fault;
wHistFlags := wHistFlags OR wHistNow;

IF xMPB_DataOK THEN
    IF xHistGot THEN
        rHistVx := MAX(rHistVx, rVibX);
        rHistVy := MAX(rHistVy, rVibY);
        rHistVz := MAX(rHistVz, rVibZ);
    ELSE
        rHistVx := rVibX;
        rHistVy := rVibY;
        rHistVz := rVibZ;
    END_IF;
    rHistTemp := rTemperature;
    xHistGot := TRUE;
END_IF;

(* ---- one sample every iHistPeriod seconds ---- *)
rtHistTick(CLK := %S6);                          (* 1 s *)
IF rtHistTick.Q THEN
    iHistSec := iHistSec + 1;
END_IF;
iHistPeriod := LIMIT(1, iHistPeriod, 3600);

IF iHistSec >= iHistPeriod THEN
    iHistSec := 0;
    aHistBuf[iHistHead, 0] := aAlmClk[2];
    aHistBuf[iHistHead, 1] := aAlmClk[1];
    IF xHistGot THEN
        aHistBuf[iHistHead, 2] := REAL_TO_INT(LIMIT(0.0, rHistVx * 100.0, 32000.0));
        aHistBuf[iHistHead, 3] := REAL_TO_INT(LIMIT(0.0, rHistVy * 100.0, 32000.0));
        aHistBuf[iHistHead, 4] := REAL_TO_INT(LIMIT(0.0, rHistVz * 100.0, 32000.0));
        aHistBuf[iHistHead, 5] := REAL_TO_INT(LIMIT(-3000.0, rHistTemp * 10.0, 3000.0));
    ELSE
        aHistBuf[iHistHead, 2] := -32768;
        aHistBuf[iHistHead, 3] := -32768;
        aHistBuf[iHistHead, 4] := -32768;
        aHistBuf[iHistHead, 5] := -32768;
    END_IF;
    aHistBuf[iHistHead, 6] := iATV_Speed;
    aHistBuf[iHistHead, 7] := WORD_TO_INT(SHL(INT_TO_WORD(aAlmClk[0]) AND 16#00FF, 8) OR (wHistFlags AND 16#00FF));
    iHistYear := aAlmClk[3];

    iHistHead := (iHistHead + 1) MOD 4320;
    IF iHistCount < 4320 THEN
        iHistCount := iHistCount + 1;
    END_IF;
    iHistSeq := (iHistSeq + 1) MOD 30000;

    (* start the next minute fresh *)
    wHistFlags := wHistNow;
    xHistGot := FALSE;
END_IF;

(* ---- a screen asks for 14 samples, or to clear ---- *)
IF aHistReq[1] <> iHistReqLast THEN
    iHistReqLast := aHistReq[1];
    IF aHistReq[0] = -1 THEN
        iHistHead := 0;
        iHistCount := 0;
        iHistSeq := 0;
        iHistSec := 0;
        aHistHdr[6] := -1;
    ELSE
        iHistSlot := LIMIT(0, aHistReq[0], 4319);
        FOR iHistI := 0 TO 13 DO
            iHistSrc := (iHistSlot + iHistI) MOD 4320;
            FOR iHistJ := 0 TO 7 DO
                aHistWin[8 * iHistI + iHistJ] := aHistBuf[iHistSrc, iHistJ];
            END_FOR;
        END_FOR;
        aHistHdr[6] := iHistSlot;
    END_IF;
    aHistHdr[7] := iHistReqLast;                 (* last: the window is ready when this matches *)
END_IF;

(* ---- the header ---- *)
aHistHdr[0] := iHistHead;
aHistHdr[1] := iHistCount;
aHistHdr[2] := iHistPeriod;
aHistHdr[3] := iHistSeq;
aHistHdr[4] := iHistYear;
aHistHdr[5] := 4320;
aHistHdr[8] := 14;
aHistHdr[9] := 1;
