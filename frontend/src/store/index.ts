import { configureStore } from "@reduxjs/toolkit";
import { setupListeners } from "@reduxjs/toolkit/query";
import { useDispatch, useSelector } from "react-redux";
import { gatewayApi } from "./gatewayApi";
import liveReducer from "./liveSlice";

export const store = configureStore({
  reducer: {
    live: liveReducer,
    [gatewayApi.reducerPath]: gatewayApi.reducer,
  },
  // Live snapshots are plain JSON, but the history can be large; skip the dev-only deep checks.
  middleware: (getDefault) =>
    getDefault({ serializableCheck: false, immutableCheck: false }).concat(gatewayApi.middleware),
});

setupListeners(store.dispatch);

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;

export const useAppDispatch = useDispatch.withTypes<AppDispatch>();
export const useAppSelector = useSelector.withTypes<RootState>();
