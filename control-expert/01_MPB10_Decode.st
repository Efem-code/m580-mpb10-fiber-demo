(* =====================================================================================
   Section 01_MPB10_Decode   (MAST task)
   Turns the MPB10's 20-byte IO-Link process data into engineering values.

   iPD_Ofs   : where port S1's process data starts inside aSIG200_In (SIG200 manual / web UI)
   iVal1_Ofs, iVal2_Ofs, rVal1_Gain, rVal2_Gain :
               PLACEHOLDERS. Take the offsets and scaling from the MPB10 IODD / operating
               instructions for the measurement mode you configured. IO-Link data is
               big-endian (high byte first).
   ===================================================================================== *)

(* 16-bit values, high byte first *)
wMPB_Alarm := SHL(BYTE_TO_WORD(aSIG200_In[iPD_Ofs]), 8)
              OR BYTE_TO_WORD(aSIG200_In[iPD_Ofs + 1]);
wMPB_Val1  := SHL(BYTE_TO_WORD(aSIG200_In[iPD_Ofs + iVal1_Ofs]), 8)
              OR BYTE_TO_WORD(aSIG200_In[iPD_Ofs + iVal1_Ofs + 1]);
wMPB_Val2  := SHL(BYTE_TO_WORD(aSIG200_In[iPD_Ofs + iVal2_Ofs]), 8)
              OR BYTE_TO_WORD(aSIG200_In[iPD_Ofs + iVal2_Ofs + 1]);

xMPB_DataOK := xSIG200_Health AND xMPB_PortActive;

IF xMPB_DataOK THEN
    rVibVelocity := INT_TO_REAL(WORD_TO_INT(wMPB_Val1)) * rVal1_Gain;   (* mm/s RMS *)
    rTemperature := INT_TO_REAL(WORD_TO_INT(wMPB_Val2)) * rVal2_Gain;   (* degC *)
    (* alarm bit meanings: confirm against the IODD *)
    xVibWarning  := wMPB_Alarm.0;
    xVibAlarm    := wMPB_Alarm.1;
    xShock       := wMPB_Alarm.2;
ELSE
    (* never show frozen-but-healthy-looking values *)
    rVibVelocity := 0.0;
    rTemperature := 0.0;
    xVibWarning  := FALSE;
    xVibAlarm    := FALSE;
    xShock       := FALSE;
END_IF;

(* PLC-side limits, so the PLC makes its own decision too (match the dashboard's Setup tab) *)
xVibWarning  := xVibWarning OR (xMPB_DataOK AND rVibVelocity >= rVibWarnLimit);
xVibAlarm    := xVibAlarm   OR (xMPB_DataOK AND rVibVelocity >= rVibAlarmLimit);
xTempWarning := xMPB_DataOK AND rTemperature >= rTempWarnLimit;
xSensorCommAlarm := NOT xMPB_DataOK;             (* fiber pulled, SIG200 off, sensor unplugged *)

rtShock(CLK := xShock);
IF rtShock.Q THEN
    IF iShockCount >= 30000 THEN iShockCount := 0; END_IF;
    iShockCount := iShockCount + 1;
END_IF;
