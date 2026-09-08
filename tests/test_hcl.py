from tfmodcache import hcl


def calls(text):
    return list(hcl.iter_module_calls(text))


def test_reads_source_and_version():
    found = calls(
        """
        module "vpc" {
          source  = "terraform-aws-modules/vpc/aws"
          version = "5.1.0"
          cidr    = "10.0.0.0/16"
        }
        """
    )
    assert [(item.name, item.source, item.version) for item in found] == [
        ("vpc", "terraform-aws-modules/vpc/aws", "5.1.0")
    ]


def test_module_without_version():
    found = calls('module "local" {\n  source = "./modules/thing"\n}\n')
    assert found[0].version is None
    assert found[0].has_version is False


def test_nested_blocks_do_not_end_the_module_block():
    found = calls(
        """
        module "outer" {
          source = "./a"
          tags = {
            team = "infra"
          }
          dynamic "rule" {
            for_each = var.rules
            content {
              name = rule.value
            }
          }
        }
        module "second" {
          source = "./b"
        }
        """
    )
    assert [item.name for item in found] == ["outer", "second"]


def test_commented_out_modules_are_ignored():
    found = calls(
        """
        # module "hash" {
        #   source = "./hash"
        # }
        // module "slash" {
        //   source = "./slash"
        // }
        /*
        module "block" {
          source = "./block"
        }
        */
        module "real" {
          source = "./real"
        }
        """
    )
    assert [item.name for item in found] == ["real"]


def test_hash_inside_a_string_does_not_start_a_comment():
    found = calls(
        'module "git" {\n'
        '  source = "git::https://example.com/repo.git?ref=v1"\n'
        '  note   = "a # inside a string"\n'
        "}\n"
    )
    assert found[0].source == "git::https://example.com/repo.git?ref=v1"


def test_module_without_a_source_is_skipped():
    assert calls('module "computed" {\n  source = var.source\n}\n') == []


def test_unbalanced_block_is_skipped_rather_than_guessed():
    assert calls('module "broken" {\n  source = "./x"\n') == []


def test_read_module_calls_reads_only_the_directory_itself(tmp_path):
    (tmp_path / "main.tf").write_text('module "a" {\n  source = "./a"\n}\n')
    (tmp_path / "notes.txt").write_text('module "ignored" {\n  source = "./x"\n}\n')
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "deep.tf").write_text('module "deep" {\n  source = "./deep"\n}\n')
    found = hcl.read_module_calls(str(tmp_path))
    assert [item.name for item in found] == ["a"]
    assert found[0].file == "main.tf"


def test_missing_directory_reads_as_empty(tmp_path):
    assert hcl.read_module_calls(str(tmp_path / "absent")) == []
