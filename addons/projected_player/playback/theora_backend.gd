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
	if _player.stream == null:
		push_warning("TheoraBackend: play() called before load().")
		return
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


## Godot's Theora playback ignores seeks (setting stream_position has no effect),
## so this backend reports no seek support.
func supports_seek() -> bool:
	return false


func seek(_seconds: float) -> void:
	pass


func get_position() -> float:
	return _player.stream_position


## get_stream_length() always returns 0 for Theora. Duration only becomes
## meaningful with an FFmpeg backend.
func get_duration() -> float:
	return _player.get_stream_length()


func _on_finished() -> void:
	state = State.IDLE
	finished.emit()
