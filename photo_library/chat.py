"""Bedrock Converse tool loop for authenticated family photo operations."""
from datetime import date, datetime
import json
import os
from zoneinfo import ZoneInfo
from .aws import client
from .intelligence import generate_search_answer, search as semantic_search
from .planner import validate_filters
from .search_query import is_simple_photo_search


def _object(properties=None, required=None):
    return {"type": "object", "properties": properties or {}, "required": required or [], "additionalProperties": False}

TOOLS = [
    {"toolSpec": {"name": "list_photos", "description": "List ready photos. Omit limit to return all available photos.", "inputSchema": {"json": _object({"limit": {"type": "integer", "minimum": 1, "maximum": 100}})}}},
    {"toolSpec": {"name": "search_photos", "description": "Analyze the natural-language photo request with Nemotron, search the family-scoped caption and label vectors, and return semantically matching photos.", "inputSchema": {"json": _object({"query": {"type": "string", "minLength": 1, "maxLength": 1000}}, ["query"])}}},
    {"toolSpec": {"name": "on_this_day", "description": "Find photos taken on this month and day in earlier years.", "inputSchema": {"json": _object({"date": {"type": "string", "format": "date"}})}}},
    {"toolSpec": {"name": "review_suggestions", "description": "Find exact duplicates, near-duplicate candidates, and quality warnings. Never delete photos.", "inputSchema": {"json": _object()}}},
    {"toolSpec": {"name": "create_album", "description": "Create a filtered album using only supported explicit photo filters.", "inputSchema": {"json": _object({"title": {"type": "string", "minLength": 1, "maxLength": 100}, "filters": _object({"person": {"type": "string", "maxLength": 100}, "since": {"type": "string", "format": "date"}, "until": {"type": "string", "format": "date"}, "orientation": {"type": "string", "enum": ["portrait", "landscape", "square"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}, "minSharpness": {"type": "number", "minimum": 0, "maximum": 255}})}, ["title", "filters"])}}},
    {"toolSpec": {"name": "list_deleted_photos", "description": "List photos in Recently deleted; do not permanently delete them.", "inputSchema": {"json": _object()}}},
    {"toolSpec": {"name": "start_slideshow", "description": "Return ready photo IDs for a family slideshow, optionally limited to IDs returned by an earlier photo search.", "inputSchema": {"json": _object({"photoIds": {"type": "array", "items": {"type": "string", "maxLength": 128}, "maxItems": 100}})}}},
    {"toolSpec": {"name": "restore_photo", "description": "Restore one photo from Recently deleted using its photoId.", "inputSchema": {"json": _object({"photoId": {"type": "string", "minLength": 1, "maxLength": 128}}, ["photoId"])}}},
    {"toolSpec": {"name": "request_trash_photo", "description": "Identify a photo the user explicitly asked to move to Recently deleted. This tool only prepares a request and never moves a photo; the browser must ask the user to confirm.", "inputSchema": {"json": _object({"photoId": {"type": "string", "minLength": 1, "maxLength": 128}}, ["photoId"])}}},
    {"toolSpec": {"name": "update_photo_details", "description": "Update user-requested people labels, capture date, or favourite status on one photo.", "inputSchema": {"json": _object({"photoId": {"type": "string", "minLength": 1, "maxLength": 128}, "people": {"type": "array", "items": {"type": "string", "maxLength": 100}, "maxItems": 30}, "capturedDate": {"type": "string", "format": "date"}, "favorite": {"type": "boolean"}}, ["photoId"])}}},
    {"toolSpec": {"name": "create_photo_comparison", "description": "Save a then-and-now comparison between two existing ready photos.", "inputSchema": {"json": _object({"originalPhotoId": {"type": "string", "minLength": 1, "maxLength": 128}, "newPhotoId": {"type": "string", "minLength": 1, "maxLength": 128}}, ["originalPhotoId", "newPhotoId"])}}},
]


def _summary(item):
    return {key: item.get(key) for key in ("photoId", "name", "caption", "labels", "status", "capturedDate", "people", "sharpness", "qualityWarnings", "favorite") if key in item}


def execute(library, user, family, name, args):
    if name == "list_photos":
        limit = args.get("limit")
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 100):
            raise ValueError("Photo limit must be 1-100")
        return [_summary(item) for item in library.photos(user, family)
                if item["status"] == "READY"][:limit]
    if name == "search_photos":
        query = args.get("query", "")
        if not isinstance(query, str) or not 1 <= len(query) <= 1000:
            raise ValueError("Search text must contain 1-1000 characters")
        matches = semantic_search(library, user, family, query)
        return [_summary(item["photo"]) for item in matches[:30]]
    if name == "on_this_day":
        day = args.get("date", datetime.now(ZoneInfo("Australia/Sydney")).date().isoformat())
        date.fromisoformat(day)
        return [_summary(item) for item in library.on_this_day(user, family, day)]
    if name == "review_suggestions":
        return library.suggestions(user, family)
    if name == "create_album":
        filters = validate_filters(args.get("filters", {}))
        return library.album(user, family, args.get("title", "Photo album"), filters)
    if name == "list_deleted_photos":
        return [_summary(item) for item in library.photos(user, family, include_trash=True) if item["status"] == "TRASHED"][:100]
    if name == "start_slideshow":
        requested = args.get("photoIds")
        available = [item for item in library.photos(user, family) if item["status"] == "READY"]
        if requested is not None:
            if not isinstance(requested, list) or len(requested) > 100 or any(not isinstance(x, str) for x in requested):
                raise ValueError("Invalid slideshow photo IDs")
            wanted = set(requested)
            available = [item for item in available if item["photoId"] in wanted]
        return [_summary(item) for item in available[:100]]
    if name == "restore_photo":
        return _summary(library.trash(user, family, args["photoId"], restore=True))
    if name == "request_trash_photo":
        item = library.photo(user, family, args["photoId"])
        if item["status"] != "READY":
            raise ValueError("Only ready photos can be moved to Recently deleted")
        return {"photo": _summary(item), "requiresUserConfirmation": True}
    if name == "update_photo_details":
        changes = {key: value for key, value in args.items() if key != "photoId"}
        if not changes:
            raise ValueError("Provide at least one photo detail to update")
        return _summary(library.update_photo(user, family, args["photoId"], changes))
    if name == "create_photo_comparison":
        return library.recreation(user, family, args["originalPhotoId"], args["newPhotoId"])
    raise ValueError("Unknown photo-library tool")


def run(library, user, family, prompt):
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 2000:
        raise ValueError("Message must contain 1-2000 characters")
    if is_simple_photo_search(prompt):
        matches = semantic_search(library, user, family, prompt)
        candidates = [item['photo'] for item in matches]
        reply, selected = generate_search_answer(prompt, candidates)
        result = [_summary(item) for item in selected]
        return {"reply": reply,
                "toolCalls": [{"name": "search_photos", "status": "completed", "input": {"query": prompt}, "result": result}]}
    model_id = os.getenv("CHAT_MODEL_ID")
    if not model_id:
        raise RuntimeError("Bedrock chat tools are not configured")
    messages = [{"role": "user", "content": [{"text": prompt.strip()}]}]
    calls_made = []
    system = [{"text": "You are a private family photo-library assistant. Use only the supplied tools for photo data. "
        "The authenticated family is fixed by the application; never ask for or change a user or family ID. "
        "Do not claim a photo was found unless a tool returned it. Never delete photos or invent photo details. "
        "For natural-language visual requests use search_photos; Nemotron interprets the request and "
        "Ground the answer in returned photo captions and labels. Do not simulate photo search by matching "
        "words in captions or labels. "
        "Use create_album only when the user asks to save or build a photo collection. "
        "For photo changes, act only on clear instructions and use the exact supplied photoId. "
        "Ask a brief clarifying question if a request cannot be mapped safely to these tools. "
        "Keep replies concise and explain which action you completed."}]
    for _ in range(4):
        response = client("bedrock-runtime").converse(modelId=model_id, system=system, messages=messages,
            toolConfig={"tools": TOOLS}, inferenceConfig={"maxTokens": 700, "temperature": 0.1})
        message = response["output"]["message"]
        messages.append(message)
        calls = [part["toolUse"] for part in message.get("content", []) if "toolUse" in part]
        if not calls:
            reply = "\n".join(part["text"] for part in message.get("content", []) if "text" in part).strip()
            return {"reply": reply or "I could not complete that request.", "toolCalls": calls_made}
        results = []
        for call in calls[:6]:
            name, call_id = call["name"], call["toolUseId"]
            try:
                result = execute(library, user, family, name, call.get("input", {}))
                output = json.dumps(result, default=str)[:14000]
                calls_made.append({"name": name, "status": "completed", "result": result})
                results.append({"toolResult": {"toolUseId": call_id, "status": "success", "content": [{"text": output}]}})
            except Exception as error:
                calls_made.append({"name": name, "status": "failed", "error": str(error)[:500]})
                results.append({"toolResult": {"toolUseId": call_id, "status": "error", "content": [{"text": f"Tool failed: {str(error)[:500]}"}]}})
        messages.append({"role": "user", "content": results})
    return {"reply": "I reached the tool-call limit for this request. Please try a narrower task.", "toolCalls": calls_made}
