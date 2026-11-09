extends Node3D

## Standalone receiver: a screen floating in front of the camera that accepts
## play commands from sender/projected_cast.py or any client following
## docs/PROTOCOL.md. Playback is limited to .ogv files under the Movies folder.


func _ready() -> void:
	var camera := Camera3D.new()
	camera.position = Vector3(0, 1.4, 3.5)
	add_child(camera)
	camera.look_at(Vector3(0, 1.4, 0))

	var quad := QuadMesh.new()
	quad.size = Vector2(3.2, 1.8)
	var mesh_instance := MeshInstance3D.new()
	mesh_instance.mesh = quad
	mesh_instance.position = Vector3(0, 1.4, 0)
	add_child(mesh_instance)

	var screen := ProjectedScreen.new()
	screen.target_mesh = mesh_instance
	add_child(screen)

	var receiver := ProjectedReceiver.new()
	receiver.screen = screen
	receiver.media_root = OS.get_system_dir(OS.SYSTEM_DIR_MOVIES)
	add_child(receiver)
