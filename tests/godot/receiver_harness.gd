extends SceneTree
## Runs receivers headless for tests/test_receiver.py. Two receivers on one screen each:
## an open one on PORT and one requiring TOKEN on PORT + 1. MEDIA_ROOT limits playback.
## Prints "READY" once both listen; quits after RUN_SECONDS or when stdin closes.

func _initialize() -> void:
	var port := int(OS.get_environment("PORT"))
	var beacon := int(OS.get_environment("BEACON_PORT"))
	var media := OS.get_environment("MEDIA_ROOT")
	var made := 0
	for spec in [{port = port, token = "", name = "Test Open"}, {port = port + 1, token = OS.get_environment("TOKEN"), name = "Test Locked"}]:
		var mesh := MeshInstance3D.new()
		mesh.mesh = QuadMesh.new()
		root.add_child(mesh)
		var screen := ProjectedScreen.new()
		screen.target_mesh = mesh
		screen.resolution = Vector2i(320, 180)
		root.add_child(screen)
		var receiver := ProjectedReceiver.new()
		receiver.screen = screen
		receiver.receiver_name = spec.name
		receiver.http_port = spec.port
		receiver.beacon_port = beacon
		receiver.media_root = media
		receiver.auth_token = spec.token
		receiver.auto_start = false
		root.add_child(receiver)
		made += 1
	await process_frame
	for n in root.get_children():
		if n is ProjectedReceiver:
			n.start()
	print("READY")
	await create_timer(float(OS.get_environment("RUN_SECONDS"))).timeout
	quit()
