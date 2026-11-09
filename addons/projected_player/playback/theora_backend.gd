class_name TheoraBackend
extends PlaybackBackend

## Default backend, built on Godot's VideoStreamPlayer.
## Plays local Ogg Theora (.ogv) files only. URLs and MP4 need another backend.

var _player: VideoStreamPlayer


func attach(parent: Node) -> void:
	_player = VideoStreamPlayer.new()
	_player.expand = true
	_player.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_player.finished.connect(_on_finished)
	parent.add_child(_player)


func load(path: String) -> bool:
	if path.get_extension().to_lower() != "ogv":
		push_warning("TheoraBackend only plays .ogv files, got: %s" % path)
		return false
	var stream := VideoStreamTheora.new()
	stream.file = path
	_player.stream = stream
	source = path
	state = State.IDLE
	return true


func play() -> void:
	_player.play()
	state = State.PLAYING


func pause() -> void:
	_player.paused = true
	state = State.PAUSED


func resume() -> void:
	_player.paused = false
	state = State.PLAYING


func stop() -> void:
	_player.stop()
	state = State.IDLE


func seek(seconds: float) -> void:
	_player.stream_position = seconds


func get_position() -> float:
	return _player.stream_position


## VideoStreamPlayer does not expose the length, so this returns 0 until an
## FFmpeg backend is added.
func get_duration() -> float:
	return 0.0


func _on_finished() -> void:
	state = State.IDLE
	finished.emit()
