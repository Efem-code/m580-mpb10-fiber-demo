(* =====================================================================================
   Section S00_IO_Map   (MAST task, FIRST section)
   The ONLY section that reads the device DDTs Control Expert created for the SIG300 and
   the ATV930 (both scanned by the BMENOC0311) and the status of the BMECRA31210 RIO drop. Everything else works on the plain variables filled in here, so if a device
   alias or DDT element name differs on our project, we fix it in this one place.

   Mapped to the device DDTs in our project (aliases ATV9xx and SIG300_Revision_1_1_from_EDS).
   The ATV9xx connection currently carries ETA and RFRD only: add LCR, OTR, THR and LFT as
   channels in its DTM, then remove the comment marks on their lines below.
   ===================================================================================== *)

(* ---------------- SICK SIG300 (EtherNet/IP, IO-Link master) ---------------- *)
xSIG300_Health := SIG300_Revision_1_1_from_EDS.Freshness;

(* The DDT names each byte instead of giving an array, so we copy the bytes we use into
   aSIG300_In at their offsets in the SIG300 input assembly: 4 = S1 PQI, 6-25 = the first
   20 bytes of S1 process data (the MPB10). *)
aSIG300_In[4] := SIG300_Revision_1_1_from_EDS.Inputs.S1_PQI;
aSIG300_In[6] := SIG300_Revision_1_1_from_EDS.Inputs.IOL_Process_Data;
aSIG300_In[7] := SIG300_Revision_1_1_from_EDS.Inputs.IOL_Process_DataD;
aSIG300_In[8] := SIG300_Revision_1_1_from_EDS.Inputs.IOL_Process_DataE;
aSIG300_In[9] := SIG300_Revision_1_1_from_EDS.Inputs.IOL_Process_DataF;
aSIG300_In[10] := SIG300_Revision_1_1_from_EDS.Inputs.IOL_Process_DataG;
aSIG300_In[11] := SIG300_Revision_1_1_from_EDS.Inputs.IOL_Process_DataH;
aSIG300_In[12] := SIG300_Revision_1_1_from_EDS.Inputs.IOL_Process_DataI;
aSIG300_In[13] := SIG300_Revision_1_1_from_EDS.Inputs.IOL_Process_DataJ;
aSIG300_In[14] := SIG300_Revision_1_1_from_EDS.Inputs.IOL_Process_DataK;
aSIG300_In[15] := SIG300_Revision_1_1_from_EDS.Inputs.IOL_Process_DataL;
aSIG300_In[16] := SIG300_Revision_1_1_from_EDS.Inputs.IOL_Process_DataM;
aSIG300_In[17] := SIG300_Revision_1_1_from_EDS.Inputs.IOL_Process_DataN;
aSIG300_In[18] := SIG300_Revision_1_1_from_EDS.Inputs.IOL_Process_DataO;
aSIG300_In[19] := SIG300_Revision_1_1_from_EDS.Inputs.IOL_Process_DataP;
aSIG300_In[20] := SIG300_Revision_1_1_from_EDS.Inputs.IOL_Process_DataQ;
aSIG300_In[21] := SIG300_Revision_1_1_from_EDS.Inputs.IOL_Process_DataR;
aSIG300_In[22] := SIG300_Revision_1_1_from_EDS.Inputs.IOL_Process_DataS;
aSIG300_In[23] := SIG300_Revision_1_1_from_EDS.Inputs.IOL_Process_DataT;
aSIG300_In[24] := SIG300_Revision_1_1_from_EDS.Inputs.IOL_Process_DataU;
aSIG300_In[25] := SIG300_Revision_1_1_from_EDS.Inputs.IOL_Process_DataV;
(* From the SIG300 EDS, input assembly 101 (404 bytes): byte 4 = S1 PQI (port qualifier),
   byte 5 reserved, bytes 6-37 = S1 process data. PQI bit 7 = process data valid. *)
xMPB_PortActive := xSIG300_Health AND ((aSIG300_In[4] AND 16#80) <> 0);

(* ---------------- Altivar ATV930 (EtherNet/IP, embedded port) ---------------- *)
xATV_Health    := ATV9xx.Freshness;
wATV_ETA       := ATV9xx.Inputs.ETA;             (* status word *)
iATV_Speed     := ATV9xx.Inputs.RFRD;            (* output speed, rpm *)
(* iATV_Current   := ATV9xx.Inputs.LCR;             motor current, 0.1 A *)
(* iATV_Torque    := ATV9xx.Inputs.OTR;             motor torque, % *)
(* iATV_Thermal   := ATV9xx.Inputs.THR;             motor thermal state, % *)
(* iATV_LastFault := ATV9xx.Inputs.LFT;             last fault code *)

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

(* ---------------- RIO drop: BMECRA31210, linked to the main rack over fiber ---------------- *)
(* The RIO main ring runs CPU ETH2/ETH3 -> BMXNRP0200 (main rack) -> fiber -> BMXNRP0200 (drop)
   -> CRA ETH2/ETH3. The drop only carries safety I/O, which belongs to the SAFE task, so this
   program reads just its health and the ring status. There is no operator panel on this board:
   xDemoEnable is set from an animation table. *)
xDrop_Health := BMEP58_ECPU_EXT.DROP_HEALTH[3];   (* our drop is number 3 on the RIO bus *)
xRing_Closed := BMEP58_ECPU_EXT.REDUNDANCY_STATUS;   (* RIO main ring: both paths OK *)
