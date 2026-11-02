(* =====================================================================================
   Section 00_IO_Map   (MAST task, FIRST section)
   The ONLY section that reads the device DDTs Control Expert created for the SIG200 and
   the ATV930 (both scanned by the BMENOC0301) and the X80 I/O on the BMECRA31210 RIO drop. Everything else works on the plain variables filled in here, so if a device
   alias or DDT element name differs on our project, we fix it in this one place.

   As shipped it compiles with the devices "not mapped": health bits FALSE, so nothing runs.
   For each line marked EDIT, replace the right-hand side with the element from the Data
   Editor (Data Editor > Variables > expand the device alias, e.g. SIG200 / ATV930):
     - health      : the connection "Freshness" bit of the device's input connection
     - input bytes : the input assembly of the SIG200 (array of bytes / words)
     - drive words : the ETA, RFRD, LCR, OTR, THR, LFT elements we set up for I/O scanning
   ===================================================================================== *)

(* ---------------- SICK SIG200 (EtherNet/IP, IO-Link master) ---------------- *)
xSIG200_Health := FALSE;                         (* EDIT: e.g. SIG200.Freshness *)

(* Copy the SIG200 input assembly into aSIG200_In (ARRAY[0..63] OF BYTE).
   If the DDT gives BYTEs, copy straight across. If it gives INT/WORDs, split each word:
   low byte first is the usual EtherNet/IP order, so check with an animation table
   that the MPB10 bytes land where the IODD says. *)
FOR iMapIdx := 0 TO 63 DO
    aSIG200_In[iMapIdx] := 0;                    (* EDIT: e.g. SIG200.Inputs.Data[iMapIdx] *)
END_FOR;
xMPB_PortActive := xSIG200_Health;               (* EDIT: AND the SIG200 "port S1 device present / PD valid" bit *)

(* ---------------- Altivar ATV930 (EtherNet/IP, embedded port) ---------------- *)
xATV_Health    := FALSE;                         (* EDIT: e.g. ATV930.Freshness *)
wATV_ETA       := 16#0000;                       (* EDIT: INT_TO_WORD(ATV930.Inputs.ETA)  status word *)
iATV_Speed     := 0;                             (* EDIT: ATV930.Inputs.RFRD  output speed, rpm *)
iATV_Current   := 0;                             (* EDIT: ATV930.Inputs.LCR   motor current, 0.1 A *)
iATV_Torque    := 0;                             (* EDIT: ATV930.Inputs.OTR   motor torque, % *)
iATV_Thermal   := 0;                             (* EDIT: ATV930.Inputs.THR   motor thermal state, % *)
iATV_LastFault := 0;                             (* EDIT: ATV930.Inputs.LFT   last fault code *)

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
