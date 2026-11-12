# -*- coding: utf-8 -*-


"""Generated protocol buffer code."""
from google.protobuf.internal import builder as _builder
from google.protobuf import descriptor as _descriptor
from google.protobuf import descriptor_pool as _descriptor_pool
from google.protobuf import symbol_database as _symbol_database


_sym_db = _symbol_database.Default()




DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n\x15\x65xecinspb/proto.proto\x12\x07\x65xecins\"4\n\x07Routine\x12\n\n\x02id\x18\x01 \x01(\t\x12\x0f\n\x07\x66n_name\x18\x02 \x01(\t\x12\x0c\n\x04\x61rgs\x18\x03 \x01(\x0c\"\xe6\x02\n\x05\x45vent\x12!\n\x04type\x18\x01 \x01(\x0e\x32\x13.execins.Event.Type\x12\x16\n\twith_data\x18\x02 \x01(\x08H\x00\x88\x01\x01\x12\x0f\n\x07ref_ids\x18\x03 \x03(\t\x12*\n\x06status\x18\x04 \x01(\x0e\x32\x15.execins.Event.StatusH\x01\x88\x01\x01\x12\x13\n\x06result\x18\x05 \x01(\x0cH\x02\x88\x01\x01\x12\x12\n\x05\x65rror\x18\x06 \x01(\tH\x03\x88\x01\x01\x12&\n\x07routine\x18\x07 \x01(\x0b\x32\x10.execins.RoutineH\x04\x88\x01\x01\"<\n\x04Type\x12\x12\n\x0eROUTINE_SUBMIT\x10\x00\x12\x0c\n\x08GET_WAIT\x10\x01\x12\x08\n\x04\x45XIT\x10\x02\x12\x08\n\x04INIT\x10\x03\"\x1c\n\x06Status\x12\x08\n\x04SUCC\x10\x00\x12\x08\n\x04\x46\x41IL\x10\x01\x42\x0c\n\n_with_dataB\t\n\x07_statusB\t\n\x07_resultB\x08\n\x06_errorB\n\n\x08_routineB%Z#github.com/ict/tide/proto/execinspbb\x06proto3')

_builder.BuildMessageAndEnumDescriptors(DESCRIPTOR, globals())
_builder.BuildTopDescriptorsAndMessages(DESCRIPTOR, 'execinspb.proto_pb2', globals())
if _descriptor._USE_C_DESCRIPTORS == False:

  DESCRIPTOR._options = None
  DESCRIPTOR._serialized_options = b'Z#github.com/ict/tide/proto/execinspb'
  _ROUTINE._serialized_start=34
  _ROUTINE._serialized_end=86
  _EVENT._serialized_start=89
  _EVENT._serialized_end=447
  _EVENT_TYPE._serialized_start=299
  _EVENT_TYPE._serialized_end=359
  _EVENT_STATUS._serialized_start=361
  _EVENT_STATUS._serialized_end=389

