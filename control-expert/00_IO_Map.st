(* =====================================================================================
   Section S00_IO_Map   (MAST task, FIRST section)
   The ONLY section that reads the device DDTs Control Expert created for the SIG300 and
   the ATV930 (both scanned by the BMENOC0301) and the X80 I/O on the BMECRA31210 RIO drop. Everything else works on the plain variables filled in here, so if a device
   alias or DDT element name differs on our project, we fix it in this one place.

   As shipped it compiles with the devices "not mapped": health bits FALSE, so nothing runs.
   For each line marked EDIT, replace the right-hand side with the element from the Data
   Editor (Data Editor > Variables > expand the device alias, e.g. SIG300 / ATV930):
     - health      : the connection "Freshness" bit of the device's input connection
     - input bytes : the input assembly of the SIG300 (array of bytes / words)
     - drive words : the ETA, RFRD, LCR, OTR, THR, LFT elements we set up for I/O scanning
   ===================================================================================== *)

(* ---------------- SICK SIG300 (EtherNet/IP, IO-Link master) ---------------- *)
xSIG300_Health := FALSE;                         (* EDIT: e.g. SIG300.Freshness *)

(* Copy the SIG300 input assembly into aSIG300_In (ARRAY[0..63] OF BYTE).
   If the DDT gives BYTEs, copy straight across. If it gives INT/WORDs, split each word:
   low byte first is the usual EtherNet/IP order, so check with an animation table
   that the MPB10 bytes land where the IODD says. *)
FOR iMapIdx := 0 TO 63 DO
    aSIG300_In[iMapIdx] := 0;                    (* EDIT: e.g. SIG300.Inputs.Data[iMapIdx] *)
END_FOR;
xMPB_PortActive := xSIG300_Health;               (* EDIT: AND the SIG300 "port S1 device present / PD valid" bit *)

(* ---------------- Altivar ATV930 (EtherNet/IP, embedded port) ---------------- *)
xATV_Health    := FALSE;                         (* EDIT: e.g. ATV930.Freshness *)
wATV_ETA       := 16#0000;                       (* EDIT: INT_TO_WORD(ATV930.Inputs.ETA)  status word *)
iATV_Speed     := 0;                             (* EDIT: ATV930.Inputs.RFRD  output speed, rpm *)
iATV_Current   := 0;                             (* EDIT: ATV930.Inputs.LCR   motor current, 0.1 A *)
iATV_Torque    := 0;                             (* EDIT: ATV930.Inputs.OTR   motor torque, % *)
iATV_Thermal   := 0;                             (* EDIT: ATV930.Inputs.THR   motor thermal state, % *)
iATV_LastFault := 0;                             (* EDIT: ATV930.Inputs.LFT   last fault code *)

(* ---------------- Testing only: PLC simulator + real drive through drive_bridge.py ----------------
   The PLC simulator can't scan the ATV930, so dashboard\drive_bridge.py does it over Modbus TCP:
   it sends our CMD, LFRD, ACC and DEC (aDash[28], [23], [30], [31]) to the drive and writes the
   drive's values back into aBridge AT %MW1200. Set xSimBridge TRUE only in the simulator; on the
   real M580 leave it FALSE (its initial value) and the DDT lines above are used.
     aBridge[0] bridge heartbeat   [1] bit 0 = drive answering   [2] ETA  [3] RFRD  [4] LCR
            [5] OTR  [6] THR  [7] LFT   [8] written HERE: 1 = this program is in bridge mode
   The bridge only drives the motor while [8] = 1 and our heartbeat (aDash[0]) is counting. *)
aBridge[8] := BOOL_TO_INT(xSimBridge);
IF xSimBridge THEN
    xBridgeHbChanged := aBridge[0] <> iBridgeHbLast;
    iBridgeHbLast    := aBridge[0];
    tonBridgeWd(IN := NOT xBridgeHbChanged, PT := t#1s);
    wBridgeStat    := INT_TO_WORD(aBridge[1]);
    xATV_Health    := NOT tonBridgeWd.Q AND wBridgeStat.0;
    wATV_ETA       := INT_TO_WORD(aBridge[2]);
    iATV_Speed     := aBridge[3];
    iATV_Current   := aBridge[4];
    iATV_Torque    := aBridge[5];
    iATV_Thermal   := aBridge[6];
    iATV_LastFault := aBridge[7];
END_IF;

(* ---------------- RIO drop: BMECRA31210 + X80 I/O (operator panel) ---------------- *)
(* X80 discrete modules on the drop give device DDTs whose channels are .DIS_CH_IN[n].VALUE
   (inputs) and .DIS_CH_OUT[n].VALUE (outputs). Check the instance names in the Data Editor. *)
xDrop_Health := FALSE;                           (* EDIT: the drop's health bit (CRA device DDT / drop status) *)
xRing_Closed := FALSE;                           (* EDIT: CPU device DDT, RIO main ring redundancy OK (both paths) *)
                                                 (*       then set xRingDiagMapped := TRUE (initial value)      *)
xPB_Start   := FALSE;                            (* EDIT: e.g. <DI module>.DIS_CH_IN[0].VALUE  Start, N/O     *)
xPB_StopNC  := TRUE;                             (* EDIT: e.g. <DI module>.DIS_CH_IN[1].VALUE  Stop, N/C      *)
                                                 (*       N/C: a broken wire reads as "pressed" and stops     *)
xPB_Reset   := FALSE;                            (* EDIT: e.g. <DI module>.DIS_CH_IN[2].VALUE  Fault reset   *)
xKeySwitch  := FALSE;                            (* EDIT: e.g. <DI module>.DIS_CH_IN[3].VALUE  Enable key    *)

(* xDemoEnable: with it FALSE the drive never gets a run command from this program.
   Until the key switch is wired, leave the next line commented and set xDemoEnable from an
   animation table. Once it is wired, remove the comment marks. *)
(* xDemoEnable := xKeySwitch AND xDrop_Health; *)
