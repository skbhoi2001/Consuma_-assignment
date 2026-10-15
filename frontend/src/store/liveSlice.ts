// Live snapshots pushed by the gateway's SSE stream (/v1/stream), one per second.
// Kept in the store so the dashboard keeps its history when you navigate away and back.

import { createSlice, type PayloadAction } from "@reduxjs/toolkit";
import type { LiveSnapshot } from "../api";

export const LIVE_HISTORY_LIMIT = 300; // 5 minutes at one snapshot per second

type LiveState = { history: LiveSnapshot[]; connected: boolean };

const initialState: LiveState = { history: [], connected: false };

const liveSlice = createSlice({
  name: "live",
  initialState,
  reducers: {
    snapshotReceived(state, action: PayloadAction<LiveSnapshot>) {
      state.history.push(action.payload);
      if (state.history.length > LIVE_HISTORY_LIMIT) {
        state.history.splice(0, state.history.length - LIVE_HISTORY_LIMIT);
      }
    },
    connectionChanged(state, action: PayloadAction<boolean>) {
      state.connected = action.payload;
    },
  },
});

export const { snapshotReceived, connectionChanged } = liveSlice.actions;
export default liveSlice.reducer;
