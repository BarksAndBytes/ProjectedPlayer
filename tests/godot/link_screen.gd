extends ProjectedScreen
## A stand-in for a screen that fetches and plays links itself (plays_links()), like FPS-CAD's
## video boards: after play() it "loads" for LOAD_SECONDS, then plays a DURATION-second video,
## or fails if the link contains "bad".

const LOAD_SECONDS := 1.0
const DURATION := 10.0

var _link := ""
var _state := "idle"
var _left := 0.0
var _position := 0.0
var _error := ""
var volume := 1.0


func _ready() -> void:
	pass  # no viewport or backend: this screen plays nothing


func plays_links() -> bool:
	return true


func load_source(source: String) -> bool:
	_link = source
	_state = "idle"
	return true


func play() -> void:
	_state = "loading"
	_left = LOAD_SECONDS
	_position = 0.0
	_error = ""


func pause() -> void:
	if _state == "playing":
		_state = "paused"


func resume() -> void:
	if _state == "paused":
		_state = "playing"


func stop() -> void:
	_state = "idle"
	_position = 0.0


func can_seek() -> bool:
	return _state == "playing" or _state == "paused"


func seek(seconds: float) -> void:
	_position = clampf(seconds, 0.0, DURATION)


func get_source() -> String:
	return _link


func get_playback_position() -> float:
	return _position


func get_last_error() -> String:
	return _error


func get_duration() -> float:
	return DURATION if can_seek() else 0.0


func set_volume(linear: float) -> void:
	volume = linear


func get_state_name() -> String:
	return _state


func _process(delta: float) -> void:
	match _state:
		"loading":
			_left -= delta
			if _left <= 0.0:
				if "bad" in _link:
					_state = "error"
					_error = "the test screen refused it"
				else:
					_state = "playing"
		"playing":
			_position = minf(_position + delta, DURATION)
