import { useEffect, useLayoutEffect, useRef, useState } from "react";

const RECONNECT_DELAY_MS = 3000;

// Subscribes to live comment events; reconnects automatically after a drop.
// viewport tells the server what is on screen, so it sends only events about that zone
// (see docs/api.md). onReconnect runs after a re-established connection: events sent
// while the socket was down are lost, so the caller should reload its data.
export function useCommentsSocket(viewport, onEvent, onReconnect) {
  const handler = useRef(onEvent);
  const reconnectHandler = useRef(onReconnect);
  const latestViewport = useRef(viewport);
  const socketRef = useRef(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    handler.current = onEvent;
    reconnectHandler.current = onReconnect;
  });

  // A layout effect runs before the effects that load data for the same render, so
  // the server already watches a zone when its data is read and no event falls between.
  useLayoutEffect(() => {
    latestViewport.current = viewport;
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(viewport));
  }, [viewport]);

  useEffect(() => {
    let socket;
    let timer;
    let stopped = false;
    let opened = false;

    const connect = () => {
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${protocol}://${window.location.host}/ws/comments/`);
      socketRef.current = socket;
      socket.onopen = () => {
        setConnected(true);
        socket.send(JSON.stringify(latestViewport.current));
        if (opened) reconnectHandler.current?.();
        opened = true;
      };
      socket.onmessage = (message) => {
        try {
          handler.current(JSON.parse(message.data));
        } catch (error) {
          console.error("Bad WebSocket message", error);
        }
      };
      socket.onclose = () => {
        setConnected(false);
        if (!stopped) timer = setTimeout(connect, RECONNECT_DELAY_MS);
      };
    };

    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      socket.close();
    };
  }, []);

  return connected;
}
