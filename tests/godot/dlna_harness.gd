extends SceneTree
## Runs DLNA renderers headless for tests/test_dlna.py: "Board One" and "Board Two", each on its
## own screen and a free port, with SSDP on SSDP_PORT and converted videos in CACHE_DIR.
## Prints "RENDERER <name> <port> <udn>" for each, then "READY". Signals are printed as
## "SIGNAL <name> <signal> <values>". Quits after RUN_SECONDS.

const Ssdp := preload("res://addons/projected_player/dlna/ssdp.gd")


func _initialize() -> void:
	Ssdp.port = int(OS.get_environment("SSDP_PORT"))
	var renderers: Array = []
	for board in ["Board One", "Board Two"]:
		var mesh := MeshInstance3D.new()
		mesh.mesh = QuadMesh.new()
		root.add_child(mesh)
		var screen := ProjectedScreen.new()
		screen.target_mesh = mesh
		screen.resolution = Vector2i(320, 180)
		root.add_child(screen)
		var renderer := ProjectedDlnaRenderer.new()
		renderer.screen = screen
		renderer.friendly_name = board
		renderer.cache_dir = OS.get_environment("CACHE_DIR").path_join(board.to_snake_case())
		renderer.ffmpeg_path = OS.get_environment("FFMPEG")
		renderer.auto_start = false
		renderer.cast_received.connect(func(uri: String, title: String) -> void: print("SIGNAL %s cast_received %s|%s" % [board, uri, title]))
		renderer.cast_failed.connect(func(reason: String) -> void: print("SIGNAL %s cast_failed %s" % [board, reason]))
		root.add_child(renderer)
		renderers.append(renderer)
	await process_frame
	for renderer in renderers:
		renderer.start()
		print("RENDERER %s %d %s" % [renderer.friendly_name, renderer.get_port(), renderer.get_udn()])
	print("SSDP listening=%s" % Ssdp.shared().listening)
	print("READY")
	await create_timer(float(OS.get_environment("RUN_SECONDS"))).timeout
	quit()
