(* =====================================================================================
   Section S01_MPB10_Decode   (MAST task)
   Turns the MPB10's 20-byte IO-Link process data into engineering values.

   iPD_Ofs : where port S1's process data starts inside aSIG300_In (6, from the SIG300 EDS)

   Layout read from the live sensor, high byte first. Check it against the MPB10 IODD:
     bytes  0-3   temperature, degC        REAL
     bytes  4-7   vibration X, mm/s        REAL
     bytes  8-11  vibration Y, mm/s        REAL
     bytes 12-15  vibration Z, mm/s        REAL
     bytes 16-19  status (bit meanings not known yet; shown raw in wMPB_Alarm)
   ===================================================================================== *)

(* the status word, shown raw on the dashboard *)
wMPB_Alarm := SHL(BYTE_TO_WORD(aSIG300_In[iPD_Ofs + 18]), 8)
              OR BYTE_TO_WORD(aSIG300_In[iPD_Ofs + 19]);

xMPB_DataOK := xSIG300_Health AND xMPB_PortActive;

(* four IEEE 754 floats, built from their bytes: 1 sign, 8 exponent, 23 mantissa bits *)
FOR iFloat := 0 TO 3 DO
    iB0 := WORD_TO_INT(BYTE_TO_WORD(aSIG300_In[iPD_Ofs + 4 * iFloat]));
    iB1 := WORD_TO_INT(BYTE_TO_WORD(aSIG300_In[iPD_Ofs + 4 * iFloat + 1]));
    iB2 := WORD_TO_INT(BYTE_TO_WORD(aSIG300_In[iPD_Ofs + 4 * iFloat + 2]));
    iB3 := WORD_TO_INT(BYTE_TO_WORD(aSIG300_In[iPD_Ofs + 4 * iFloat + 3]));
    iExpo := (iB0 MOD 128) * 2 + iB1 / 128;
    IF (iExpo = 0) OR (iExpo = 255) THEN
        rFloat := 0.0;                           (* zero, or not a number *)
    ELSE
        rFloat := 1.0 + DINT_TO_REAL(INT_TO_DINT(iB1 MOD 128) * 65536
                                     + INT_TO_DINT(iB2) * 256 + INT_TO_DINT(iB3)) / 8388608.0;
        FOR iPow := 1 TO iExpo - 127 DO
            rFloat := rFloat * 2.0;
        END_FOR;
        FOR iPow := 1 TO 127 - iExpo DO
            rFloat := rFloat / 2.0;
        END_FOR;
        IF iB0 >= 128 THEN
            rFloat := -rFloat;
        END_IF;
    END_IF;
    aMPB_Value[iFloat] := rFloat;
END_FOR;

IF xMPB_DataOK THEN
    rTemperature := aMPB_Value[0];               (* degC *)
    rVibX := aMPB_Value[1];                      (* mm/s *)
    rVibY := aMPB_Value[2];
    rVibZ := aMPB_Value[3];
    rVibVelocity := rVibX;                       (* the worst axis *)
    IF rVibY > rVibVelocity THEN rVibVelocity := rVibY; END_IF;
    IF rVibZ > rVibVelocity THEN rVibVelocity := rVibZ; END_IF;
ELSE
    (* never show frozen-but-healthy-looking values *)
    rVibVelocity := 0.0;
    rVibX := 0.0;
    rVibY := 0.0;
    rVibZ := 0.0;
    rTemperature := 0.0;
END_IF;
xShock := FALSE;                                 (* no shock source until we know the status bits *)

(* PLC-side limits, so the PLC makes its own decision too (match the dashboard's Setup tab) *)
xVibWarning  := xMPB_DataOK AND rVibVelocity >= rVibWarnLimit;
xVibAlarm    := xMPB_DataOK AND rVibVelocity >= rVibAlarmLimit;
xTempWarning := xMPB_DataOK AND rTemperature >= rTempWarnLimit;
xSensorCommAlarm := NOT xMPB_DataOK;             (* SIG300 cable pulled or off, sensor unplugged *)

rtShock(CLK := xShock);
IF rtShock.Q THEN
    IF iShockCount >= 30000 THEN iShockCount := 0; END_IF;
    iShockCount := iShockCount + 1;
END_IF;
