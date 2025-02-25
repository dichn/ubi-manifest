import pytest
from pubtools.pulplib import ModulemdUnit, RpmDependency, RpmUnit
from testfixtures import LogCapture

from ubi_manifest.worker.tasks.depsolver.models import (
    DepsolverItem,
    PackageToExclude,
    UbiUnit,
)
from ubi_manifest.worker.tasks.depsolver.rpm_depsolver import (
    BATCH_SIZE_RESOLVER,
    Depsolver,
)

from .utils import create_and_insert_repo, rpmdeps_from_names


def test_what_provides(pulp):
    """tests querying for provides in pulp"""
    depsolver = Depsolver(None, None, None)

    requires = [RpmDependency(name="gcc")]

    repo = create_and_insert_repo(id="test_repo_id", pulp=pulp)

    unit_1 = RpmUnit(
        name="test",
        version="10",
        release="200",
        epoch="1",
        arch="x86_64",
        provides=[RpmDependency(name="gcc")],
    )

    unit_2 = RpmUnit(
        name="test",
        version="100",
        release="200",
        epoch="1",
        arch="x86_64",
        provides=[RpmDependency(name="gcc")],
    )

    pulp.insert_units(repo, [unit_1, unit_2])

    result = depsolver.what_provides(requires, [repo], [])
    # there is only one unit in the result with the highest version
    assert len(result) == 1
    unit = result[0]
    assert unit.version == "100"
    assert unit.provides[0].name == "gcc"


def test_extract_and_resolve():
    """test extracting provides and requires from RPM units"""
    depsolver = Depsolver(None, None, None)

    # set initial data to depsolver instance
    depsolver._requires = rpmdeps_from_names("pkg_a", "pkg_b")
    depsolver._provides = rpmdeps_from_names("pkg_c", "pkg_d")
    depsolver._unsolved = rpmdeps_from_names("pkg_a", "pkg_b")

    unit = RpmUnit(
        name="test",
        version="10",
        release="200",
        epoch="1",
        arch="x86_64",
        provides=[RpmDependency(name="pkg_e"), RpmDependency(name="pkg_b")],
        requires=[RpmDependency(name="pkg_f"), RpmDependency(name="(pkg_g if pkg_h)")],
    )

    depsolver.extract_and_resolve([unit])
    # internal state of depsolver should change
    # pkg_f, pkg_g and pkg_h are new requirements that are added to the requires set
    assert depsolver._requires == rpmdeps_from_names(
        "pkg_a", "pkg_b", "pkg_f", "pkg_g", "pkg_h"
    )
    # pkg_e and pkg_b are added to the provides set
    assert depsolver._provides == rpmdeps_from_names("pkg_c", "pkg_d", "pkg_e", "pkg_b")
    # pkg_b is resolved but pkg_f, pkg_g and pkg_h are added as new unsolved requirement
    assert depsolver._unsolved == rpmdeps_from_names("pkg_a", "pkg_f", "pkg_g", "pkg_h")


def test_get_base_packages(pulp):
    """test queries for input packages for given repo"""
    depsolver = Depsolver(None, None, None)

    repo = create_and_insert_repo(id="test_repo_id", pulp=pulp)

    unit_1 = RpmUnit(
        name="test",
        version="10",
        release="200",
        epoch="1",
        arch="x86_64",
    )

    unit_2 = RpmUnit(
        name="test",
        version="100",
        release="200",
        epoch="1",
        arch="x86_64",
    )

    unit_3 = RpmUnit(
        name="test-exclude",
        version="100",
        release="200",
        epoch="1",
        arch="x86_64",
    )

    pulp.insert_units(repo, [unit_1, unit_2, unit_3])

    pkgs_to_search = ["test", "test-exclude"]
    blacklist = [
        PackageToExclude("test-exc", globbing=True),
        PackageToExclude("test", globbing=False, arch="s390x"),
    ]

    result = depsolver.get_base_packages([repo], pkgs_to_search, blacklist)
    # there should be only one package in result with the highest version
    assert len(result) == 1
    unit = result[0]
    assert unit.name == "test"
    assert unit.version == "100"


def test_get_pkgs_from_all_modules(pulp):
    """tests getting pkgs filenames from all available modulemd units"""
    depsolver = Depsolver(None, None, None)

    repo = create_and_insert_repo(id="test_repo_1", pulp=pulp)

    unit_1 = ModulemdUnit(
        name="test",
        stream="10",
        version=100,
        context="abcdef",
        arch="x86_64",
        artifacts=[
            "perl-version-7:0.99.24-441.module+el8.3.0+6718+7f269185.src",
            "perl-version-7:0.99.24-441.module+el8.3.0+6718+7f269185.x86_64",
        ],
    )
    unit_2 = ModulemdUnit(
        name="test",
        stream="20",
        version=100,
        context="abcdef",
        arch="x86_64",
        artifacts=[
            "perl-version-7:1.99.24-441.module+el8.4.0+9911+7f269185.src",
            "perl-version-7:1.99.24-441.module+el8.4.0+9911+7f269185.x86_64",
        ],
    )

    pulp.insert_units(repo, [unit_1, unit_2])

    ft = depsolver._get_pkgs_from_all_modules([repo])

    result = ft.result()

    # there are 4 filenames according from 2 modulemd units
    expected_filenames = set(
        [
            "perl-version-0.99.24-441.module+el8.3.0+6718+7f269185.src.rpm",
            "perl-version-0.99.24-441.module+el8.3.0+6718+7f269185.x86_64.rpm",
            "perl-version-1.99.24-441.module+el8.4.0+9911+7f269185.src.rpm",
            "perl-version-1.99.24-441.module+el8.4.0+9911+7f269185.x86_64.rpm",
        ]
    )

    assert len(result) == 4
    assert result == expected_filenames


@pytest.mark.parametrize(
    "items, expected_batch_size",
    [
        (BATCH_SIZE_RESOLVER + 1, BATCH_SIZE_RESOLVER),
        (BATCH_SIZE_RESOLVER - 1, BATCH_SIZE_RESOLVER - 1),
    ],
)
def test_batch_size(items, expected_batch_size):
    """test proper calculation of a batch size"""
    depsolver = Depsolver(None, None, None)
    depsolver._unsolved = {x for x in range(items)}

    batch_size = depsolver._batch_size()

    assert batch_size == expected_batch_size


def test_run(pulp):
    """test the main method of depsolver"""
    repos, repo_srpm, expected_output_set = _prepare_test_data(pulp)

    blacklist_1 = [
        PackageToExclude("lib_exclude"),
        PackageToExclude("blacklisted-", globbing=True),
    ]
    blacklist_2 = [PackageToExclude("base_pkg_to_exclude")]

    whitelist_1 = set(["gcc", "jq", "perl-version"])
    dep_item_1 = DepsolverItem(
        whitelist=whitelist_1,
        blacklist=blacklist_1,
        in_pulp_repos=[repos[0]],
    )

    whitelist_2 = set(
        [
            "apr",
            "babel",
            "base_pkg_to_exclude",
        ]
    )  # simulate blacklisting a package that was wrongly put into whitelist
    dep_item_2 = DepsolverItem(
        whitelist=whitelist_2,
        blacklist=blacklist_2,
        in_pulp_repos=[repos[1]],
    )

    module_rpms = {
        "perl-version-1.99.24-441.module+el8.4.0+9911+7f269185.x86_64.rpm",
        "yaml-version-0.99.24-441.module+el8.4.0+9911+7f269185.src.rpm",
        "perl-version-0.99.24-441.module+el8.3.0+6718+7f269185.x86_64.rpm",
    }

    with LogCapture() as mock_log:
        with Depsolver([dep_item_1, dep_item_2], [repo_srpm], module_rpms) as depsolver:
            depsolver.run()

            # check internal state of depsolver object
            # provides set holds all capabilities that we went through during depsolving
            assert depsolver._provides == rpmdeps_from_names(
                "gcc",
                "jq",
                "apr",
                "babel",
                "lib.a",
                "lib.b",
                "lib.c",
                "lib.d",
                "lib.e",
                "lib.f",
                "lib.z",
            )

            # requires set holds all requires that we went through during depsolving
            assert depsolver._requires == rpmdeps_from_names(
                "blacklisted-package",
                "lib.a",
                "lib.b",
                "lib.c",
                "lib.d",
                "lib.e",
                "lib.g",
                "lib_exclude",
                "lib.z",
                "pkgX(abc)",
                "capY(xyz)",
            )

            # unsolved set should be empty after depsolving finishes
            # it will be emptied even if we have unsolvable dependency
            assert len(depsolver._unsolved) == 0

            # there are unsolved requires, we can get those by
            unsolved = {req.name for req in depsolver._requires} - {
                prov.name for prov in depsolver._provides
            }
            # there are exactly 5 unresolved deps, lib_exclude and blacklisted-package unsolved due to blacklisting
            assert unsolved == set(
                [
                    "pkgX(abc)",
                    "capY(xyz)",
                    "lib.g",
                    "lib_exclude",
                    "blacklisted-package",
                ]
            )

            # checking correct rpm and srpm names and its associate source repo id
            output = [
                (item.name, item.associate_source_repo_id)
                for item in depsolver.output_set | depsolver.srpm_output_set
            ]
            assert sorted(output) == expected_output_set

            # Check logs produced by failed depsolving
            mock_log.check_present(
                (
                    "ubi_manifest.worker.tasks.depsolver.rpm_depsolver",
                    "WARNING",
                    "Failed depsolving: lib.g can not be found in these input repos:"
                    " ['test_repo_1', 'test_repo_2']. These rpms depend on it ['lib-x-100-200.x86_64.rpm']",
                ),
                (
                    "ubi_manifest.worker.tasks.depsolver.rpm_depsolver",
                    "WARNING",
                    "Failed depsolving: lib_exclude is blacklisted. These rpms depend on it"
                    " ['lib-x-100-200.x86_64.rpm', 'lib-y-100-200.x86_64.rpm']",
                ),
                (
                    "ubi_manifest.worker.tasks.depsolver.rpm_depsolver",
                    "WARNING",
                    "Failed depsolving: blacklisted-package is blacklisted."
                    " These rpms depend on it ['lib-y-100-200.x86_64.rpm']",
                ),
                (
                    "ubi_manifest.worker.tasks.depsolver.rpm_depsolver",
                    "WARNING",
                    "Failed depsolving: pkgX(abc) can not be found in these input repos:"
                    " ['test_repo_1', 'test_repo_2']. These rpms depend on it ['lib-x-100-200.x86_64.rpm']",
                ),
                (
                    "ubi_manifest.worker.tasks.depsolver.rpm_depsolver",
                    "WARNING",
                    "Failed depsolving: capY(xyz) can not be found in these input repos:"
                    " ['test_repo_1', 'test_repo_2']. These rpms depend on it ['lib-x-100-200.x86_64.rpm']",
                ),
                order_matters=False,
            )


def _prepare_test_data(pulp):
    repo_1 = create_and_insert_repo(id="test_repo_1", pulp=pulp)

    repo_2 = create_and_insert_repo(id="test_repo_2", pulp=pulp)

    repo_srpm = create_and_insert_repo(id="test_repo_srpm", pulp=pulp)

    unit_1 = RpmUnit(
        name="gcc",
        version="10",
        release="200",
        epoch="1",
        arch="x86_64",
        provides=[RpmDependency(name="gcc"), RpmDependency(name="lib.a")],
        requires=[RpmDependency(name="lib.b"), RpmDependency(name="lib.c")],
        sourcerpm="gcc.src.rpm",
    )

    unit_2 = RpmUnit(
        name="jq",
        version="100",
        release="200",
        epoch="1",
        arch="x86_64",
        provides=[RpmDependency(name="jq")],
        requires=[
            RpmDependency(name="lib.a"),
            RpmDependency(name="lib.d"),
            RpmDependency(name="/some/script"),
        ],
    )
    # note: the dependency "/some/script" will be skipped from processing

    unit_3 = RpmUnit(
        name="apr",
        version="100",
        release="200",
        epoch="1",
        arch="x86_64",
        provides=[RpmDependency(name="apr")],
        requires=[RpmDependency(name="lib.a"), RpmDependency(name="lib.d")],
    )

    unit_4 = RpmUnit(
        name="babel",
        version="100",
        release="200",
        epoch="1",
        arch="x86_64",
        provides=[RpmDependency(name="babel"), RpmDependency(name="lib.b")],
        requires=[RpmDependency(name="lib.a"), RpmDependency(name="lib.b")],
    )

    unit_5 = RpmUnit(
        name="lib-x",
        version="100",
        release="200",
        epoch="1",
        arch="x86_64",
        filename="lib-x-100-200.x86_64.rpm",
        provides=[RpmDependency(name="lib.c"), RpmDependency(name="lib.d")],
        requires=[
            RpmDependency(name="lib.e"),
            RpmDependency(name="lib.g"),
            RpmDependency(name="( pkgX(abc) with capY(xyz) )"),
            RpmDependency(name="lib_exclude"),
        ],
    )

    unit_6 = RpmUnit(
        name="lib-y",
        version="100",
        release="200",
        epoch="1",
        arch="x86_64",
        provides=[RpmDependency(name="lib.e"), RpmDependency(name="lib.f")],
        requires=[
            RpmDependency(name="lib_exclude"),
            RpmDependency(name="blacklisted-package"),
        ],
        sourcerpm="lib-y.src.rpm",
        filename="lib-y-100-200.x86_64.rpm",
    )

    unit_7 = RpmUnit(
        name="lib_exclude",
        version="100",
        release="200",
        epoch="1",
        arch="x86_64",
        provides=[],
        requires=[],
    )

    unit_8 = RpmUnit(
        name="base_pkg_to_exclude",
        version="100",
        release="200",
        epoch="1",
        arch="x86_64",
        provides=[],
        requires=[],
    )

    unit_9 = RpmUnit(
        name="gcc-source",
        filename="gcc.src.rpm",
        version="100",
        release="200",
        epoch="1",
        arch="x86_64",
        provides=[],
        requires=[],
        content_type_id="srpm",
    )

    unit_10 = RpmUnit(
        name="lib-y-source",
        filename="lib-y.src.rpm",
        version="100",
        release="200",
        epoch="1",
        arch="x86_64",
        provides=[],
        requires=[],
        content_type_id="srpm",
    )

    md_unit_1 = ModulemdUnit(
        name="test",
        stream="10",
        version=100,
        context="abcdef",
        arch="x86_64",
        artifacts=[
            "perl-version-7:0.99.24-441.module+el8.3.0+6718+7f269185.src",
            "perl-version-7:0.99.24-441.module+el8.3.0+6718+7f269185.x86_64",
        ],
    )
    md_unit_2 = ModulemdUnit(
        name="test",
        stream="20",
        version=100,
        context="abcdef",
        arch="x86_64",
        artifacts=[
            "perl-version-7:1.99.24-441.module+el8.4.0+9911+7f269185.src",
            "perl-version-7:1.99.24-441.module+el8.4.0+9911+7f269185.x86_64",
        ],
    )
    # unit_11a/b are modular units, both of them has to be added to the output set
    # because they're listed on some module's artifacts
    unit_11a = RpmUnit(
        name="perl-version",
        filename="perl-version-1.99.24-441.module+el8.4.0+9911+7f269185.x86_64.rpm",
        version="1.99.24",
        release="441.module+el8.4.0+9911+7f269185",
        epoch="1",
        arch="x86_64",
        provides=[],
        requires=[
            RpmDependency(name="lib.z"),
        ],
    )

    unit_11b = RpmUnit(
        name="perl-version",
        filename="perl-version-0.99.24-441.module+el8.3.0+6718+7f269185.x86_64.rpm",
        version="0.99.24",
        release="441.module+el8.3.0+6718+7f269185",
        epoch="0",
        arch="x86_64",
        provides=[],
        requires=[],
    )

    # 11c package is non-modular variant of 11a/b that needs to be in the output set
    unit_11c = RpmUnit(
        name="perl-version",
        filename="perl-version-0-1.x86_64.rpm",
        version="0",
        release="1",
        epoch="0",
        arch="x86_64",
        provides=[],
        requires=[],
    )

    unit_12 = RpmUnit(
        name="lib-z",
        version="100",
        release="200",
        epoch="1",
        arch="x86_64",
        provides=[RpmDependency(name="lib.z")],
        requires=[],
    )

    unit_13 = RpmUnit(
        name="yaml",
        filename="yaml-version-0.99.24-441.module+el8.4.0+9911+7f269185.src.rpm",
        version="000",
        release="099",
        epoch="1",
        arch="x86_64",
        provides=[],
        requires=[],
        content_type_id="srpm",
    )

    unit_14 = RpmUnit(
        name="blacklisted-package",
        version="100",
        release="200",
        epoch="1",
        arch="x86_64",
        provides=[],
        requires=[],
    )

    repo_1_units = [unit_1, unit_2, unit_5, unit_11a, unit_11b, unit_11c, unit_12]
    repo_2_units = [unit_3, unit_4, unit_6]
    repo_srpm_units = [unit_9, unit_10, unit_13]

    pulp.insert_units(repo_1, repo_1_units + [md_unit_1, md_unit_2])
    pulp.insert_units(
        repo_2, repo_2_units + [unit_7, unit_8, unit_14]
    )  # add extra units, that will be excluded by blacklist

    pulp.insert_units(repo_srpm, repo_srpm_units)

    expected_output_set = (
        [(unit.name, "test_repo_1") for unit in repo_1_units]
        + [(unit.name, "test_repo_2") for unit in repo_2_units]
        + [(unit.name, "test_repo_srpm") for unit in repo_srpm_units]
    )

    return [repo_1, repo_2], repo_srpm, sorted(expected_output_set)


def test_export():
    """
    Tests that exported units from depsolver includes identical rpms, if they come
    from different repos.
    """
    depsolver = Depsolver(None, None, None)

    rpm = RpmUnit(
        name="test",
        filename="test-1.rpm",
        version="0",
        release="0",
        epoch="1",
        arch="x86_64",
    )
    srpm = RpmUnit(
        name="test",
        filename="test-1.src.rpm",
        version="0",
        release="0",
        epoch="1",
        arch="x86_64",
    )

    # identical rpm - two times from repo test_repo_1, one in test_repo_2
    unit_1 = UbiUnit(rpm, "test_repo_1")
    unit_2 = UbiUnit(rpm, "test_repo_1")
    unit_3 = UbiUnit(rpm, "test_repo_2")

    # identical srpm - two times from repo test_repo_3, one in test_repo_4
    unit_4 = UbiUnit(srpm, "test_repo_3")
    unit_5 = UbiUnit(srpm, "test_repo_3")
    unit_6 = UbiUnit(srpm, "test_repo_4")

    depsolver.output_set = set([unit_1, unit_2, unit_3])
    depsolver.srpm_output_set = set([unit_4, unit_5, unit_6])

    # export output_set from depsolver
    exported = depsolver.export()

    # check that:
    # 1. there are only unique s/rpms for one repo
    # 2. it's allowed to have identical rpms from different repos
    rpms = exported["test_repo_1"]
    assert len(rpms) == 1
    assert rpms[0]._unit is rpm

    rpms = exported["test_repo_2"]
    assert len(rpms) == 1
    assert rpms[0]._unit is rpm

    rpms = exported["test_repo_3"]
    assert len(rpms) == 1
    assert rpms[0]._unit is srpm

    rpms = exported["test_repo_4"]
    assert len(rpms) == 1
    assert rpms[0]._unit is srpm


def test_run_modular_deps(pulp):
    """test the main method of depsolver using scenario when a non-modular RPM can theoretically be resolved a modular
    RPM dependency, but we need to resolve a non-modular RPMs with
    non-modular dependency otherwise we could end with uninstallable
    package"""
    (
        repo,
        all_requires,
        all_provides,
        expected_output_set,
    ) = _prepare_test_data_modular_test(pulp)

    whitelist = set(["nginx"])
    dep_item = DepsolverItem(
        whitelist=whitelist,
        blacklist=[],
        in_pulp_repos=[repo],
    )

    module_rpms = {
        "nginx-1.22.1-3.module+el9.2.0+17617+2f289c6c.x86_64.rpm",
        "nginx-core-1.22.1-3.module+el9.2.0+17617+2f289c6c.x86_64.rpm",
    }

    with Depsolver([dep_item], [], module_rpms) as depsolver:
        depsolver.run()
        # check internal state of depsolver object
        # provides set holds all capabilities that we went through during depsolving
        assert depsolver._provides == all_provides

        # requires set holds all requires that we went through during depsolving
        assert depsolver._requires == all_requires

        # unsolved set should be empty after depsolving finishes
        # it will be emptied even if we have unsolvable dependency
        assert len(depsolver._unsolved) == 0

        # there are unsolved requires, we can get those by
        unsolved = {req.name for req in depsolver._requires} - {
            prov.name for prov in depsolver._provides
        }
        # there is no unsolved dep.
        assert len(unsolved) == 0

        # checking correct rpm filenames and its associate source repo id
        output = [
            (item.filename, item.associate_source_repo_id)
            for item in depsolver.output_set | depsolver.srpm_output_set
        ]

        assert sorted(output) == expected_output_set


def _prepare_test_data_modular_test(pulp):
    rpm_non_mod_1_provide = RpmDependency(
        epoch="1",
        flags="EQ",
        name="nginx",
        release="14.el9",
        version="1.20.1",
    )
    rpm_non_mod_1_require = RpmDependency(
        epoch="1",
        flags="EQ",
        name="nginx-core",
        release="14.el9",
        version="1.20.1",
    )
    rpm_non_mod_1 = RpmUnit(
        name="nginx",
        filename="nginx-1.20.1-14.el9.x86_64.rpm",
        version="1.20.1",
        release="14.el9",
        epoch="1",
        arch="x86_64",
        provides=[rpm_non_mod_1_provide],
        requires=[rpm_non_mod_1_require],
        content_type_id="rpm",
    )
    rpm_non_mod_2_provide = RpmDependency(
        epoch="1",
        flags="EQ",
        name="nginx-core",
        release="14.el9",
        version="1.20.1",
    )

    rpm_non_mod_2 = RpmUnit(
        name="nginx-core",
        filename="nginx-core-1.20.1-14.el9.x86_64.rpm",
        version="1.20.1",
        release="14.el9",
        epoch="1",
        arch="x86_64",
        provides=[rpm_non_mod_2_provide],
        requires=[],
        content_type_id="rpm",
    )

    md_unit = ModulemdUnit(
        name="nginx",
        stream="1.22",
        version=9020020221218004026,
        context="9",
        arch="x86_64",
        artifacts=[
            "nginx-1:1.22.1-3.module+el9.2.0+17617+2f289c6c.x86_64",
            "nginx-core-1:1.22.1-3.module+el9.2.0+17617+2f289c6c.x86_64",
        ],
    )
    rpm_mod_1_provide = RpmDependency(
        epoch="1",
        flags="EQ",
        name="nginx",
        release="3.module+el9.2.0+17617+2f289c6c",
        version="1.22.1",
    )
    rpm_mod_1_require = RpmDependency(
        epoch="1",
        flags="EQ",
        name="nginx-core",
        release="3.module+el9.2.0+17617+2f289c6c",
        version="1.22.1",
    )
    rpm_mod_1 = RpmUnit(
        name="nginx",
        filename="nginx-1.22.1-3.module+el9.2.0+17617+2f289c6c.x86_64.rpm",
        version="1",
        release="3.module+el9.2.0+17617+2f289c6c",
        epoch="1.22.1",
        arch="x86_64",
        provides=[rpm_mod_1_provide],
        requires=[rpm_mod_1_require],
        content_type_id="rpm",
    )
    rpm_mod_2_provide = RpmDependency(
        epoch="1",
        flags="EQ",
        name="nginx-core",
        release="3.module+el9.2.0+17617+2f289c6c",
        version="1.22.1",
    )
    rpm_mod_2 = RpmUnit(
        name="nginx-core",
        filename="nginx-core-1.22.1-3.module+el9.2.0+17617+2f289c6c.x86_64.rpm",
        version="1.22.1",
        release="3.module+el9.2.0+17617+2f289c6c",
        epoch="1",
        arch="x86_64",
        provides=[rpm_mod_2_provide],
        requires=[],
        content_type_id="rpm",
    )

    repo = create_and_insert_repo(id="test_repo", pulp=pulp)

    rpms = [rpm_non_mod_1, rpm_non_mod_2, rpm_mod_1, rpm_mod_2]

    pulp.insert_units(repo, rpms + [md_unit])

    # all rpms are expected to be in the output set: all non-modular and all modular
    expected_output_set = [(unit.filename, "test_repo") for unit in rpms]
    all_provides = set(
        [
            rpm_non_mod_1_provide,
            rpm_non_mod_2_provide,
            rpm_mod_1_provide,
            rpm_mod_2_provide,
        ]
    )
    all_requires = set([rpm_non_mod_1_require, rpm_mod_1_require])

    return repo, all_requires, all_provides, sorted(expected_output_set)


def test_run_with_skipped_depsolving(pulp):
    """
    Tests that while using 'base-pkgs-only: True' flag in ubi config file,
    depsolving for RPMs is not run and only pkgs from config file are exported
    in output. Also guessing names of debug pkgs is skipped.
    """
    rpm_rpm, repo_srpm, expected_output_set = _prepare_test_data_skip_depsolving(pulp)

    whitelist = set(["gcc", "jq", "perl-version"])
    dep_item = DepsolverItem(
        whitelist=whitelist,
        blacklist=[],
        in_pulp_repos=[rpm_rpm],
    )

    flags = {
        "base_pkgs_only": True,
    }
    with Depsolver([dep_item], [repo_srpm], [], **flags) as depsolver:
        depsolver.run()
        # check internal state of depsolver object
        # with provided flag base_pkgs_only:True we don't store any of provides|requires
        assert depsolver._provides == set()

        assert depsolver._requires == set()

        assert len(depsolver._unsolved) == 0

        # checking correct rpm and srpm names and its associate source repo id
        output = [
            (item.name, item.associate_source_repo_id)
            for item in depsolver.output_set | depsolver.srpm_output_set
        ]

        assert sorted(output) == expected_output_set


def _prepare_test_data_skip_depsolving(pulp):
    repo_rpm = create_and_insert_repo(id="test_repo_rpm", pulp=pulp)
    repo_srpm = create_and_insert_repo(id="test_repo_srpm", pulp=pulp)

    unit_1 = RpmUnit(
        name="gcc",
        version="10",
        release="200",
        epoch="1",
        arch="x86_64",
        provides=[RpmDependency(name="lib.a")],
        requires=[
            RpmDependency(name="dep-gcc"),
            RpmDependency(name="lib.b"),
            RpmDependency(name="lib.c"),
        ],
        sourcerpm="gcc.src.rpm",
    )

    unit_2 = RpmUnit(
        name="dep-gcc",
        version="100",
        release="200",
        epoch="1",
        arch="x86_64",
        provides=[RpmDependency(name="dep-gcc")],
        requires=[
            RpmDependency(name="lib.a"),
            RpmDependency(name="lib.b"),
        ],
        sourcerpm="dep-gcc.src.rpm",
    )
    # note: the dependency "/some/script" will be skipped from processing

    unit_1_srpm = RpmUnit(
        name="gcc",
        filename="gcc.src.rpm",
        version="1",
        release="1",
        epoch="1",
        arch="x86_64",
        provides=[],
        requires=[],
        content_type_id="srpm",
    )

    unit_2_srpm = RpmUnit(
        name="dep-gcc",
        filename="dep-gcc.src.rpm",
        version="1",
        release="1",
        epoch="1",
        arch="x86_64",
        provides=[],
        requires=[],
        content_type_id="srpm",
    )

    pulp.insert_units(repo_rpm, [unit_1, unit_2])
    pulp.insert_units(repo_srpm, [unit_1_srpm, unit_2_srpm])

    expected_output_set = [(unit.name, "test_repo_rpm") for unit in [unit_1]] + [
        (unit.name, "test_repo_srpm") for unit in [unit_1_srpm]
    ]

    return repo_rpm, repo_srpm, sorted(expected_output_set)


def test_log_missing_base_pkgs(pulp):
    repo_rpm = create_and_insert_repo(id="test_repo_rpm", pulp=pulp)
    unit_1 = RpmUnit(
        name="gcc",
        version="10",
        release="200",
        epoch="1",
        arch="x86_64",
        sourcerpm="gcc.src.rpm",
        provides=[],
        requires=[],
    )
    pulp.insert_units(repo_rpm, [unit_1])

    with LogCapture() as mock_log:
        whitelist = set(["gcc", "jq", "perl-version"])
        dep_item = DepsolverItem(
            whitelist=whitelist,
            blacklist=[],
            in_pulp_repos=[repo_rpm],
        )

        with Depsolver([dep_item], [], []) as depsolver:
            depsolver.run()
            # logger should warn when the pkgs from whitelist weren't found
            mock_log.check_present(
                (
                    "ubi_manifest.worker.tasks.depsolver.rpm_depsolver",
                    "WARNING",
                    "'jq' not found in ['test_repo_rpm'].",
                ),
                (
                    "ubi_manifest.worker.tasks.depsolver.rpm_depsolver",
                    "WARNING",
                    "'perl-version' not found in ['test_repo_rpm'].",
                ),
                order_matters=False,
            )
            # check the output for the only `gcc` package
            output = [
                (item.name, item.associate_source_repo_id)
                for item in depsolver.output_set | depsolver.srpm_output_set
            ]
            assert output == [("gcc", "test_repo_rpm")]
