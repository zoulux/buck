import collections
import os
import random

from artificialproject.random import weighted_choice


class GenerationFailedException(Exception):
    pass


GeneratedField = collections.namedtuple('GeneratedField', [
    'value',
    'deps',
])


class NullableGenerator:
    def __init__(self, value_generator):
        self._value_generator = value_generator
        self._null_values = collections.Counter()

    def add_sample(self, base_path, sample):
        if sample is None:
            self._null_values.update([True])
        else:
            self._null_values.update([False])
            self._value_generator.add_sample(base_path, sample)

    def generate(self):
        if weighted_choice(self._null_values):
            return GeneratedField(None, [])
        else:
            return self._value_generator.generate()


class SingletonGenerator:
    def __init__(self, set_generator):
        self._set_generator = set_generator

    def add_sample(self, base_path, sample):
        self._set_generator.add_sample(base_path, [sample])

    def generate(self):
        field = self._set_generator.generate()
        assert len(field.value) == 1, field
        return GeneratedField(field.value[0], field.deps)


class StringGenerator:
    def __init__(self):
        self._lengths = collections.Counter()
        self._first_chars = collections.Counter()
        self._other_chars = collections.Counter()

    def add_sample(self, base_path, sample):
        self._lengths.update([len(sample)])
        if sample:
            self._first_chars.update(sample[0])
        for ch in sample[1:]:
            self._other_chars.update(ch)

    def generate(self):
        length = weighted_choice(self._lengths)
        output = ''
        if length > 0:
            output += weighted_choice(self._first_chars)
        while len(output) < length:
            output += weighted_choice(self._other_chars)
        return GeneratedField(output, [])


class BuildTargetSetGenerator:
    def __init__(self, context):
        self._context = context
        self._lengths = collections.Counter()
        self._types = collections.Counter()

    def add_sample(self, base_path, sample):
        self._lengths.update([len(sample)])
        for target in sample:
            target = target.split('#')[0]
            if target.startswith(':'):
                target = '//' + base_path + target
            target_data = self._context.input_target_data[target]
            self._types.update([target_data['buck.type']])

    def generate(self, force_length=None):
        if force_length is not None:
            length = force_length
        else:
            length = weighted_choice(self._lengths)
        type_counts = collections.Counter()
        types = collections.Counter(self._types)
        for i in range(length):
            type_counts.update([weighted_choice(types)])
        output = []
        for type, count in type_counts.items():
            options = self._context.gen_targets_by_type[type]
            if count > len(options):
                raise GenerationFailedException()
            output.extend(random.sample(options, count))
        return GeneratedField(output, output)


class PathSetGenerator:
    def __init__(self, context):
        self._context = context
        self._component_generator = StringGenerator()
        self._lengths = collections.Counter()
        self._component_counts = collections.Counter()

    def add_sample(self, base_path, sample):
        self._lengths.update([len(sample)])
        for path in sample:
            components = []
            while path:
                path, component = os.path.split(path)
                components.append(component)
            self._component_counts.update([len(components)])
            for component in components:
                self._component_generator.add_sample(base_path, component)

    def generate(self, force_length=None):
        if force_length is not None:
            length = force_length
        else:
            length = weighted_choice(self._lengths)
        output = [self._generate_path() for i in range(length)]
        return GeneratedField(output, [])

    def _generate_path(self):
        component_count = weighted_choice(self._component_counts)
        components = [self._component_generator.generate().value
                      for i in range(component_count)]
        path = os.path.join(*components)
        full_path = os.path.join(self._context.output_repository, path)
        if os.path.exists(full_path):
            raise GenerationFailedException()
        try:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
        except (NotADirectoryError, FileExistsError):
            raise GenerationFailedException()
        with open(full_path, 'w'):
            pass
        return path


class SourcePathSetGenerator:
    def __init__(self, context):
        self._build_target_set_generator = BuildTargetSetGenerator(context)
        self._path_set_generator = PathSetGenerator(context)
        self._lengths = collections.Counter()
        self._build_target_values = collections.Counter()

    def add_sample(self, base_path, sample):
        self._lengths.update([len(sample)])
        for source_path in sample:
            if source_path.startswith('//') or source_path.startswith(':'):
                self._build_target_values.update([True])
                self._build_target_set_generator.add_sample(
                        base_path, [source_path])
            else:
                self._build_target_values.update([False])
                self._path_set_generator.add_sample(base_path, [source_path])

    def generate(self):
        length = weighted_choice(self._lengths)
        build_target_count = 0
        path_count = 0
        for i in range(length):
            if weighted_choice(self._build_target_values):
                build_target_count += 1
            else:
                path_count += 1
        build_targets = self._build_target_set_generator.generate(
                force_length=build_target_count)
        paths = self._path_set_generator.generate(force_length=path_count)
        assert len(build_targets.value) == build_target_count, (
                build_targets, build_target_count)
        assert len(paths.value) == path_count, (paths, path_count)
        return GeneratedField(
                build_targets.value + paths.value,
                build_targets.deps + paths.deps)


class SourcesWithFlagsGenerator:
    def __init__(self, context):
        self._source_path_set_generator = SourcePathSetGenerator(context)
        self._flag_generator = StringGenerator()
        self._flag_counts = collections.Counter()

    def add_sample(self, base_path, sample):
        source_paths = []
        flag_lists = []
        for source_with_flags in sample:
            if isinstance(source_with_flags, list):
                source_paths.append(source_with_flags[0])
                flag_lists.append(source_with_flags[1])
            else:
                source_paths.append(source_with_flags)
                flag_lists.append([])
        self._source_path_set_generator.add_sample(base_path, source_paths)
        for flags in flag_lists:
            self._flag_counts.update([len(flags)])
            for flag in flags:
                self._flag_generator.add_sample(base_path, flag)

    def generate(self):
        source_paths = self._source_path_set_generator.generate()
        output = [self._generate_source_with_flags(sp)
                  for sp in source_paths.value]
        return GeneratedField(output, source_paths.deps)

    def _generate_source_with_flags(self, source_path):
        flag_count = weighted_choice(self._flag_counts)
        if flag_count == 0:
            return source_path
        flags = [self._flag_generator.generate().value
                 for i in range(flag_count)]
        return [source_path, flags]
