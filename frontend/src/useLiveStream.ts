import { useEffect } from "react";
import type { LiveSnapshot } from "./api";
import { useAppDispatch, useAppSelector } from "./store";
import { connectionChanged, snapshotReceived } from "./store/liveSlice";

/** Opens the gateway's Server-Sent Events stream once and feeds every snapshot into the store.
 *  Mounted at app level, so history keeps growing while you're on other pages. */
export function useLiveStreamConnection() {
  const dispatch = useAppDispatch();
  useEffect(() => {
    const source = new EventSource("/v1/stream");
    source.onopen = () => dispatch(connectionChanged(true));
    source.onerror = () => dispatch(connectionChanged(false));
    source.addEventListener("snapshot", (event) => {
      dispatch(snapshotReceived(JSON.parse((event as MessageEvent).data) as LiveSnapshot));
    });
    return () => source.close();
  }, [dispatch]);
}

/** The last snapshots received from the stream (read from the store). */
export function useLiveStream() {
  const history = useAppSelector((s) => s.live.history);
  const connected = useAppSelector((s) => s.live.connected);
  return { history, latest: history.at(-1), connected };
}
