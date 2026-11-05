"""Application-scoped Windows media controls with playback-state read-back."""

import asyncio
import time
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from clickachu.tools.base import Tier, Tool, ToolResult


class MediaArgs(BaseModel):
    app: str = Field(min_length=1, description="Media application, e.g. Spotify")
    action: Literal["play", "pause", "status"] = "status"
    launch: bool = Field(default=False, description="Open the app if no media session exists")


def simple_media_request(text: str) -> MediaArgs | None:
    """Only shortcut unambiguous generic playback. Specific songs stay with the UI agent."""
    from clickachu.agent.goals import request_goal

    goal = request_goal(text)
    if goal and goal.kind == "media" and goal.target:
        return MediaArgs(app=goal.target, action=goal.operation, launch=goal.launch)
    return None


async def resolve_media_target(requested="", previous="", operation="play"):
    if requested:
        return requested
    manager = await media_manager()
    sessions = list(manager.get_sessions())
    if previous and any(
        previous.casefold() in s.source_app_user_model_id.casefold() for s in sessions
    ):
        return previous
    playing = [s for s in sessions if playback_status(s) == "playing"]
    candidates = playing if operation == "pause" and len(playing) == 1 else sessions
    if len(candidates) == 1:
        return candidates[0].source_app_user_model_id
    if not candidates:
        raise ValueError("Which app should play the music? No media player is available.")
    raise ValueError("Which media app do you mean? More than one player is available.")


async def media_manager():
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager

    return await GlobalSystemMediaTransportControlsSessionManager.request_async()


def matching_session(manager, app):
    # Never fall back to the current session: it could belong to another application.
    sessions = [
        s for s in manager.get_sessions() if app.casefold() in s.source_app_user_model_id.casefold()
    ]
    if len(sessions) > 1:
        raise ValueError(f"Multiple {app} media sessions; select a player in the app first.")
    return sessions[0] if sessions else None


def playback_status(session):
    return session.get_playback_info().playback_status.name.casefold()


class ControlMediaTool(Tool[MediaArgs]):
    name: ClassVar[str] = "control_media"
    description: ClassVar[str] = (
        "Play/resume, pause, or read playback in a specific Windows media app. "
        "Verifies transport state. Resumes its current queue; use UI search for a specific song."
    )
    Args: ClassVar[type[BaseModel]] = MediaArgs
    tier: ClassVar[Tier] = Tier.LOW
    timeout_s: ClassVar[float] = 30

    async def run(self, args, ctx):
        try:
            manager = await media_manager()
            session = matching_session(manager, args.app)
            launched = False
            if session is None and args.launch and args.action == "play":
                launched = True
                from clickachu.tools.desktop import OpenAppArgs, OpenAppTool

                opened = await ctx.workers.run(
                    "com", OpenAppTool().run, OpenAppArgs(name=args.app), ctx, timeout=10
                )
                if not opened.ok:
                    return opened
                deadline = time.monotonic() + 5
                while session is None and time.monotonic() < deadline:
                    await asyncio.sleep(0.25)
                    session = matching_session(manager, args.app)
            if session is None:
                return ToolResult(
                    ok=False,
                    summary=(
                        f"{args.app} has no available media session. "
                        "Open a track or sign in in the app, then try again."
                    ),
                )
            status = playback_status(session)
            expected = {"play": "playing", "pause": "paused"}.get(args.action)
            if expected and status != expected:
                method = (
                    session.try_play_async if args.action == "play" else session.try_pause_async
                )
                accepted = await method()
                # A just-launched player registers its media session before it is ready to
                # obey transport commands, so reissue the command while it warms up. A running
                # player that refuses the command is reported immediately.
                wait_s = 10 if launched else 3 if accepted else 0
                deadline = time.monotonic() + wait_s
                next_retry = time.monotonic() + 1.0
                while status != expected and time.monotonic() < deadline:
                    await asyncio.sleep(0.15)
                    status = playback_status(session)
                    if status != expected and launched and time.monotonic() >= next_retry:
                        # try_play/try_pause are idempotent, so reissuing them is safe.
                        await method()
                        next_retry = time.monotonic() + 1.0
                if status != expected:
                    return ToolResult(
                        ok=False,
                        summary=(
                            f"{args.app} did not confirm {expected} playback (state: {status}). "
                            "Inspect the live app controls before choosing another method."
                        ),
                    )
            if expected:
                # A brief second read catches startup transitions. Do not ask the model to
                # poll/reissue a completed command indefinitely or monitor later user changes.
                await asyncio.sleep(0.2)
                status = playback_status(session)
                if status != expected:
                    return ToolResult(
                        ok=False,
                        summary=(
                            f"{args.app} changed to {status}; "
                            f"{expected} playback was not confirmed."
                        ),
                    )
            title = artist = ""
            try:
                properties = await asyncio.wait_for(session.try_get_media_properties_async(), 1)
                if properties:
                    title, artist = properties.title, properties.artist
            except (OSError, TimeoutError):
                pass  # Playback read-back is still valid if optional metadata is unavailable.
            track = f" — {title}" if title else ""
            if artist:
                track += f" by {artist}"
            summary = f"{args.app}: {status}{track}."
            return ToolResult(
                ok=True,
                summary=summary,
                data={
                    "app": args.app,
                    "status": status,
                    "title": title,
                    "artist": artist,
                    "state_verified": True,
                },
            )
        except (OSError, RuntimeError, ValueError, ImportError) as exc:
            return ToolResult(ok=False, summary=f"Could not control {args.app} playback: {exc}")

    def verify(self, args, result):
        return bool(result.ok and result.data and result.data.get("state_verified"))
