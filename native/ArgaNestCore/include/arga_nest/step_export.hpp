#pragma once

#include "packer.hpp"

#include <string>

namespace arga::core {

/**
 * STEP AP203 mínimo: extruye cada polígono exterior como sólido prismático.
 * No requiere OCCT (escritor ASCII propio). Suficiente para robot pick de cajas/prismas.
 * Para heal/XCAF complejo, el bridge Python puede usar CAD(OCCT) como upgrade path.
 */
std::string step_from_pack_result(const PackResult& result, double thickness_mm);

}  // namespace arga::core
